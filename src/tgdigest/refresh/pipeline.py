"""The refresh run: steps in order, status after each, one message at the end.

``run_refresh`` only orchestrates — the steps are closures over the real collector, ingest,
dedup, index and profile code (``default_steps``), so the orchestration is tested with fakes.
A failed step stops the run: later steps depend on earlier ones (no index without enrichment).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from tgdigest.refresh.report import render
from tgdigest.refresh.state import RefreshFiles, RefreshStatus, StepStatus, utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tgdigest.config import Settings

log = structlog.get_logger(__name__)

Progress = Callable[[dict[str, Any]], None]
StepFn = Callable[[Progress], Awaitable[dict[str, Any]]]
Notify = Callable[[list[int], str], Awaitable[None]]

PASS_TOPICS = ("cinema",)
"""Links and entities were enriched for cinema only as shipped; the refresh keeps that."""
EMBED_THREADS = 8
"""BGE-M3 threads in the index step — the corpus was embedded at 8; llama-server has 16."""
PROGRESS_EVERY_S = 15.0


def recipients(status: RefreshStatus, allowed: set[int]) -> list[int]:
    """Who hears about the run: the requester always; everyone allowed when a scheduled run
    fails (a quiet success at night is not news)."""
    if status.trigger == "bot" and status.requested_by is not None:
        return [status.requested_by]
    if status.state == "failed":
        return sorted(allowed)
    return []


class _StepProgress:
    """What a step calls with its running totals; the file is rewritten at most every 15 s."""

    def __init__(self, files: RefreshFiles, status: RefreshStatus, step: StepStatus) -> None:
        self.files, self.status, self.step = files, status, step
        self.last_write = time.monotonic()

    def __call__(self, result: dict[str, Any]) -> None:
        self.step.result = result
        if time.monotonic() - self.last_write >= PROGRESS_EVERY_S:
            self.last_write = time.monotonic()
            self.files.write_status(self.status)


async def run_refresh(
    files: RefreshFiles,
    steps: Sequence[tuple[str, StepFn]],
    *,
    trigger: str,
    requested_by: int | None = None,
    notify: Notify | None = None,
    allowed: set[int] | None = None,
) -> RefreshStatus:
    status = RefreshStatus(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        trigger=trigger,
        requested_by=requested_by,
        started_at=utcnow(),
    )
    files.write_status(status)
    log.info("refresh_started", run_id=status.run_id, trigger=trigger)
    for name, fn in steps:
        step = StepStatus(name=name, started_at=utcnow())
        status.steps.append(step)
        status.step = name
        files.write_status(status)
        t0 = time.monotonic()
        try:
            step.result = await fn(_StepProgress(files, status, step))
            step.state = "ok"
        except Exception as exc:  # a failed step ends the run, never the process silently
            step.state = "failed"
            status.state = "failed"
            status.error = f"{name}: {exc!r}"[:500]
            log.exception("refresh_step_failed", step=name)
        finally:
            step.finished_at = utcnow()
            step.seconds = round(time.monotonic() - t0, 1)
            files.write_status(status)
        log.info("refresh_step", step=name, state=step.state, seconds=step.seconds, **step.result)
        if step.state == "failed":
            break
    else:
        status.state = "ok"
    status.step = None
    status.finished_at = utcnow()
    files.write_status(status)
    files.append_history(status)
    log.info("refresh_finished", run_id=status.run_id, state=status.state)
    chats = recipients(status, allowed or set())
    if notify is not None and chats:
        try:
            await notify(chats, render(status.to_json(), running=False))
        except Exception:  # the run is done either way; the status file has it
            log.exception("refresh_notify_failed")
    return status


# --- the real steps -----------------------------------------------------------------------


def default_steps(
    settings: Settings, factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> list[tuple[str, StepFn]]:
    from sqlalchemy import select

    from tgdigest.db.models import Channel, Feedback

    since = now - timedelta(days=settings.refresh_window_days)
    views_since = now - timedelta(days=settings.refresh_views_days)
    shared: dict[str, Any] = {}

    async def channels() -> list[Channel]:
        async with factory() as session:
            stmt = select(Channel).where(Channel.is_active).order_by(Channel.topic, Channel.id)
            return list((await session.execute(stmt)).scalars())

    async def collect(progress: Progress) -> dict[str, Any]:
        from tgdigest.collector.cli import make_source
        from tgdigest.collector.media import MediaStore
        from tgdigest.collector.sync import link_forwards, sync_channel

        targets = await channels()
        totals = {"channels": len(targets), "inserted": 0, "media": 0, "flood_waits": 0}
        failed: list[str] = []
        store = MediaStore(settings.media_dir)
        async with make_source(settings) as src:
            for ch in targets:  # one channel at a time, as the collector always does
                try:
                    st = await sync_channel(factory, src, store, ch.id)
                except Exception as exc:  # one channel must not cost the night
                    failed.append(ch.username)
                    log.warning("refresh_channel_failed", channel=ch.username, error=repr(exc))
                    continue
                totals["inserted"] += st.inserted
                totals["media"] += st.media_downloaded
                totals["flood_waits"] += st.flood_waits
                progress({**totals, "errors": len(failed)})
        if failed and len(failed) == len(targets):
            raise RuntimeError(f"every channel failed (Telegram unreachable?): {failed[:3]}")
        async with factory() as session:
            linked = await link_forwards(session)
        return {**totals, "errors": len(failed), "failed_channels": failed, "forwards": linked}

    async def views(progress: Progress) -> dict[str, Any]:
        from tgdigest.collector.cli import make_source
        from tgdigest.collector.sync import refresh_channel

        if settings.refresh_views_days == 0:
            return {"skipped": True}
        totals = {"channels": 0, "counters_updated": 0, "texts_fixed": 0, "enrichment_reset": 0}
        async with make_source(settings) as src:
            for ch in await channels():
                try:
                    st = await refresh_channel(factory, src, ch.id, since=views_since)
                except Exception as exc:
                    log.warning("refresh_views_failed", channel=ch.username, error=repr(exc))
                    continue
                totals["channels"] += 1
                totals["counters_updated"] += st.counters_updated
                totals["texts_fixed"] += st.texts_fixed
                totals["enrichment_reset"] += st.enrichment_reset
                progress(dict(totals))
        return totals

    async def ingest(progress: Progress) -> dict[str, Any]:
        from tgdigest.ingest.graph import IngestDeps
        from tgdigest.ingest.runner import IngestStats, VisionStats, run_ingest, run_vision_pass
        from tgdigest.ingest.vision_cache import VisionCache
        from tgdigest.llm.client import LLMClient

        llm = LLMClient(settings)
        cache = VisionCache(settings.data_dir / "refresh" / "vision.jsonl")
        out: dict[str, Any] = {}

        def seen(v: VisionStats) -> None:
            out.update(
                phase="vision",
                posts=v.posts,
                images=v.images,
                images_done=v.cached + v.done,
                images_failed=v.failed,
            )
            progress(dict(out))

        async def free_slot_for_vision() -> None:
            await unload_model(settings, settings.llm_model_fast)

        # pass 1: every image with the vision model, which has the router's free slot to itself
        vision = await run_vision_pass(
            factory,
            IngestDeps(llm=llm, media_root=settings.media_dir, vision_cache=cache),
            cache,
            since=since,
            missing_only=True,
            concurrency=settings.refresh_vision_concurrency,
            progress=seen,
            before=free_slot_for_vision,
        )
        seen(vision)
        # pass 2: classification with the text model; captions and OCR come from the cache
        if vision.done:
            await unload_model(settings, settings.vlm_model)

        def classified(st: IngestStats) -> None:
            out.update(
                phase="classify",
                selected=st.selected,
                processed=st.processed,
                failed=len(st.failed),
                model_version=st.model_version,
            )
            progress(dict(out))

        stats = await run_ingest(
            factory,
            IngestDeps(llm=llm, media_root=settings.media_dir, vision_cache=cache.cache_only()),
            since=since,
            missing_only=True,
            concurrency=settings.refresh_concurrency,
            progress=classified,
        )
        classified(stats)
        if stats.selected and not stats.processed:
            raise RuntimeError(f"no post enriched out of {stats.selected} (model server down?)")
        cache.clear()  # the next run starts from what is still pending, not from old reads
        return dict(out)

    async def links(progress: Progress) -> dict[str, Any]:
        from tgdigest.ingest.passes import run_links
        from tgdigest.llm.client import LLMClient

        out = {"selected": 0, "summarized": 0, "failed": 0}
        for topic in PASS_TOPICS:
            st = await run_links(
                factory,
                LLMClient(settings),
                topic=topic,
                since=since,
                concurrency=settings.refresh_concurrency,
                proxy=settings.agent_proxy,
            )
            out["selected"] += st.selected
            out["summarized"] += st.with_result
            out["failed"] += len(st.failed)
        return out

    async def entities(progress: Progress) -> dict[str, Any]:
        from tgdigest.ingest.passes import run_entities
        from tgdigest.llm.client import LLMClient

        out = {"selected": 0, "with_entities": 0, "failed": 0}
        for topic in PASS_TOPICS:
            st = await run_entities(
                factory,
                LLMClient(settings),
                topic=topic,
                since=since,
                concurrency=settings.refresh_concurrency,
            )
            out["selected"] += st.selected
            out["with_entities"] += st.with_result
            out["failed"] += len(st.failed)
        return out

    async def dedup(progress: Progress) -> dict[str, Any]:
        from tgdigest.dedup.runner import compute_signatures, rebuild_clusters

        sig = await compute_signatures(factory, settings.media_dir)
        progress({"signatures": sig.computed})
        st = await rebuild_clusters(factory)  # the CLI's defaults: pHash ≤ 6, frequency ≤ 12
        return {
            "signatures": sig.computed,
            "clusters": st.clusters,
            "clustered": st.clustered_posts,
        }

    def post_index() -> Any:
        if "index" not in shared:
            from qdrant_client import QdrantClient

            from tgdigest.retrieval.embeddings import BGEM3Embedder
            from tgdigest.retrieval.index import PostIndex

            shared["index"] = PostIndex(
                QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=EMBED_THREADS)
            )
        return shared["index"]

    async def index(progress: Progress) -> dict[str, Any]:
        from tgdigest.retrieval.runner import build_index

        idx = post_index()
        st = await build_index(factory, idx)  # whole corpus: new clusters touch old payloads
        return {
            "indexed": st.indexed,
            "payload_updated": st.payload_updated,
            "embedded_texts": st.embedded_texts,
            "total": idx.count(),
        }

    async def profiles(progress: Progress) -> dict[str, Any]:
        from tgdigest.profile.runner import build_and_store_profile

        async with factory() as session:
            users = sorted(set((await session.scalars(select(Feedback.user_id))).all()))
        for uid in users:  # the bot's 👍/👎 only add rows; this is where they take effect
            await build_and_store_profile(factory, post_index(), uid)
        return {"users": len(users)}

    return [
        ("collect", collect),
        ("views", views),
        ("ingest", ingest),
        ("links", links),
        ("entities", entities),
        ("dedup", dedup),
        ("index", index),
        ("profiles", profiles),
    ]


async def unload_model(settings: Settings, model: str) -> bool:
    """Free a model's slot in the shared router before the other tier needs it, so the router
    never evicts on its own — its pick could be the assistant. "Not running" is fine."""
    import httpx

    root = settings.llm_base_url.removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{root}/models/unload", json={"model": model})
    except httpx.HTTPError as exc:
        log.warning("model_unload_failed", model=model, error=repr(exc))
        return False
    if resp.status_code == 200 or (resp.status_code == 400 and "not running" in resp.text):
        log.info("model_unloaded", model=model, was_loaded=resp.status_code == 200)
        return True
    log.warning("model_unload_failed", model=model, status=resp.status_code, body=resp.text[:200])
    return False


async def telegram_notify(settings: Settings, chats: list[int], text: str) -> None:
    if settings.bot_token is None:
        return
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.enums import ParseMode

    session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else None
    bot = Bot(
        settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for chat in chats:
            await bot.send_message(chat, text, disable_web_page_preview=True)
    finally:
        await bot.session.close()


async def run_default(
    settings: Settings, files: RefreshFiles, *, trigger: str, requested_by: int | None
) -> RefreshStatus:
    from tgdigest.db.base import make_engine, make_session_factory

    engine = make_engine(settings.database_url)
    try:
        steps = default_steps(settings, make_session_factory(engine), now=datetime.now(UTC))

        async def notify(chats: list[int], text: str) -> None:
            await telegram_notify(settings, chats, text)

        return await run_refresh(
            files,
            steps,
            trigger=trigger,
            requested_by=requested_by,
            notify=notify,
            allowed=settings.allowed_user_ids,
        )
    finally:
        await engine.dispose()
