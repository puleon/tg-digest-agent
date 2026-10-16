"""Runs the ingest graph over posts that lack enrichment for the current ``model_version``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.db.upsert import insert_or_update
from tgdigest.ingest.graph import (
    IngestDeps,
    IngestState,
    Member,
    build_ingest_graph,
    describe_member,
    image_members,
    model_version,
    needs_vision,
)
from tgdigest.ingest.vision_cache import VisionCache

log = structlog.get_logger(__name__)

ENRICHMENT_COLUMNS = (
    "ocr_text",
    "vlm_caption",
    "topic_labels",
    "is_ad",
    "is_spoiler",
    "quality_score",
    "injection_flag",
    "enriched_at",
    "model_version",
)


@dataclass
class IngestStats:
    model_version: str
    selected: int = 0
    """Posts (albums count once), not message rows."""
    processed: int = 0
    degraded: dict[str, int] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failed: list[int] = field(default_factory=list)


@dataclass
class VisionStats:
    posts: int = 0
    """Pending posts (albums count once)."""
    images: int = 0
    """Image messages of those posts that need reading."""
    cached: int = 0
    """Already read by an earlier run that stopped before classifying."""
    done: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


async def select_pending(
    session: AsyncSession,
    version: str,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    force: bool = False,
    missing_only: bool = False,
) -> list[Post]:
    """Newest first, so a bounded run always covers the freshest posts; ``since`` limits the
    history depth (the first version of the system needs three months, not six).
    ``missing_only`` ignores the version: only posts with no enrichment row at all, so a run
    with another model (the refresh reads images with the text model) never re-enriches the
    corpus that an earlier version already covered."""
    stmt = (
        select(Post)
        .join(Channel, Channel.id == Post.channel_id)
        .options(selectinload(Post.channel))
        .order_by(Post.posted_at.desc(), Post.id)
    )
    if missing_only:
        stmt = stmt.outerjoin(Enrichment, Enrichment.post_id == Post.id).where(
            Enrichment.post_id.is_(None)
        )
    elif not force:  # idempotent: rows already enriched by this exact version are skipped
        stmt = stmt.outerjoin(
            Enrichment, (Enrichment.post_id == Post.id) & (Enrichment.model_version == version)
        ).where(Enrichment.post_id.is_(None))
    if topic:
        stmt = stmt.where(Channel.topic == topic)
    if since is not None:
        stmt = stmt.where(Post.posted_at >= since)
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().unique())


async def album_members(session: AsyncSession, post: Post) -> list[Post]:
    """Every message of the post's album (the post itself for a plain message), by id."""
    if post.grouped_id is None:
        return [post]
    stmt = (
        select(Post)
        .where(Post.channel_id == post.channel_id, Post.grouped_id == post.grouped_id)
        .order_by(Post.id)
    )
    return list((await session.execute(stmt)).scalars())


async def group_posts(session: AsyncSession, pending: list[Post]) -> list[IngestState]:
    """Pending rows → one graph input per post; albums are assembled once from all members."""
    seen: set[tuple[int, int]] = set()
    inputs: list[IngestState] = []
    for post in pending:
        key = (post.channel_id, post.grouped_id if post.grouped_id is not None else -post.id)
        if key in seen:
            continue
        seen.add(key)
        members = await album_members(session, post)
        text = "\n".join(m.text for m in members if m.text)
        inputs.append(
            {
                "post_id": members[0].id,
                "members": [
                    Member(post_id=m.id, media_type=m.media_type, media_path=m.media_path)
                    for m in members
                ],
                "channel_topic": post.channel.topic,
                "text": text,
                "is_forward": any(m.forward_from_channel is not None for m in members),
            }
        )
    return inputs


def to_rows(state: IngestState, version: str) -> list[dict[str, Any]]:
    """One enrichment row per member: its own caption/OCR, the post's shared labels."""
    labels = state.get("labels") or {}
    vision = state.get("vision") or {}
    now = datetime.now(UTC)
    rows = []
    for member in state.get("members", []):
        v = vision.get(member["post_id"]) or {}
        rows.append(
            {
                "post_id": member["post_id"],
                "ocr_text": v.get("ocr_text") or None,
                "vlm_caption": v.get("caption") or None,
                "topic_labels": [labels["topic"]] if labels else None,
                "is_ad": labels.get("is_ad"),
                "is_spoiler": labels.get("is_spoiler"),
                "quality_score": float(labels["quality"]) if labels else None,
                "injection_flag": state.get("injection_flag", False),
                "enriched_at": now,
                "model_version": version,
            }
        )
    return rows


async def run_ingest(
    factory: async_sessionmaker[AsyncSession],
    deps: IngestDeps,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    force: bool = False,
    missing_only: bool = False,
    concurrency: int = 2,
    progress: Callable[[IngestStats], None] | None = None,
) -> IngestStats:
    """``progress`` is called after every post (processed or failed) — the refresh job shows
    it in the bot while a multi-hour run is going."""
    version = model_version(deps)
    graph = build_ingest_graph(deps)
    stats = IngestStats(model_version=version)
    async with factory() as session:
        posts = await select_pending(
            session,
            version,
            topic=topic,
            since=since,
            limit=limit,
            force=force,
            missing_only=missing_only,
        )
        inputs = await group_posts(session, posts)
    stats.selected = len(inputs)
    sem = asyncio.Semaphore(concurrency)

    async def one(inp: IngestState) -> None:
        async with sem:
            try:
                state: IngestState = await graph.ainvoke(inp)
            except Exception as exc:  # a single post must never stop the run
                log.error("ingest_failed", post_id=inp["post_id"], error=repr(exc)[:300])
                stats.failed.append(inp["post_id"])
                if progress is not None:
                    progress(stats)
                return
            async with factory() as session:
                await session.execute(
                    insert_or_update(
                        session,
                        Enrichment.__table__,  # type: ignore[arg-type]
                        to_rows(state, version),
                        index_elements=["post_id"],
                        update_columns=list(ENRICHMENT_COLUMNS),
                    )
                )
                await session.commit()
            stats.processed += 1
            for d in state.get("degraded", []):
                stats.degraded[d] = stats.degraded.get(d, 0) + 1
            usage = state.get("usage") or {}
            stats.prompt_tokens += usage.get("prompt_tokens", 0)
            stats.completion_tokens += usage.get("completion_tokens", 0)
            if progress is not None:
                progress(stats)

    await asyncio.gather(*(one(i) for i in inputs))
    log.info("ingest_done", **{k: v for k, v in vars(stats).items() if k != "failed"})
    return stats


async def run_vision_pass(
    factory: async_sessionmaker[AsyncSession],
    deps: IngestDeps,
    cache: VisionCache,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    missing_only: bool = False,
    concurrency: int = 4,
    progress: Callable[[VisionStats], None] | None = None,
    before: Callable[[], Awaitable[Any]] | None = None,
) -> VisionStats:
    """Read the images of every pending post that needs them into ``cache`` — the first of the
    refresh's two passes, so the vision model is loaded once, not swapped in for every post.
    ``run_ingest`` with ``cache.cache_only()`` then classifies without touching it.
    ``before`` runs only when there is an image to read (the refresh frees a model slot)."""
    async with factory() as session:
        posts = await select_pending(
            session,
            model_version(deps),
            topic=topic,
            since=since,
            limit=limit,
            missing_only=missing_only,
        )
        inputs = await group_posts(session, posts)
    todo = [m for inp in inputs if needs_vision(inp) for m in image_members(inp)]
    pending = [m for m in todo if m["post_id"] not in cache]
    stats = VisionStats(posts=len(inputs), images=len(todo), cached=len(todo) - len(pending))
    sem = asyncio.Semaphore(concurrency)
    if pending and before is not None:
        await before()

    async def one(member: Member) -> None:
        async with sem:
            try:
                result, usage, notes = await describe_member(deps, member)
            except Exception as exc:  # one image must never stop the pass
                log.error("vision_pass_failed", post_id=member["post_id"], error=repr(exc)[:300])
                result, usage, notes = None, {}, ["vision:failed"]
            cache.put(member["post_id"], result, notes)
            stats.done += 1
            stats.failed += result is None
            stats.prompt_tokens += usage.get("prompt_tokens", 0)
            stats.completion_tokens += usage.get("completion_tokens", 0)
            if progress is not None:
                progress(stats)

    await asyncio.gather(*(one(m) for m in pending))
    log.info("vision_pass_done", **vars(stats))
    return stats
