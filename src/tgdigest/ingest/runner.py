"""Runs the ingest graph over posts that lack enrichment for the current ``model_version``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.db.upsert import insert_or_update
from tgdigest.ingest.graph import IngestDeps, IngestState, build_ingest_graph, model_version

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
    processed: int = 0
    degraded: dict[str, int] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failed: list[int] = field(default_factory=list)


async def select_pending(
    session: AsyncSession,
    version: str,
    *,
    topic: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> list[Post]:
    stmt = (
        select(Post)
        .join(Channel, Channel.id == Post.channel_id)
        .options(selectinload(Post.channel))
        .order_by(Post.posted_at.desc(), Post.id)
    )
    if not force:  # idempotent: rows already enriched by this exact version are skipped
        stmt = stmt.outerjoin(
            Enrichment, (Enrichment.post_id == Post.id) & (Enrichment.model_version == version)
        ).where(Enrichment.post_id.is_(None))
    if topic:
        stmt = stmt.where(Channel.topic == topic)
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().unique())


async def album_text(session: AsyncSession, post: Post) -> str:
    """Albums carry the caption on one message: use the group's text for every member."""
    if post.grouped_id is None or post.text:
        return post.text
    stmt = select(Post.text).where(
        Post.channel_id == post.channel_id, Post.grouped_id == post.grouped_id, Post.text != ""
    )
    return "\n".join((await session.execute(stmt)).scalars())


def to_input(post: Post, text: str) -> IngestState:
    return {
        "post_id": post.id,
        "channel_topic": post.channel.topic,
        "text": text,
        "media_type": post.media_type,
        "media_path": post.media_path,
        "is_forward": post.forward_from_channel is not None,
    }


def to_row(post_id: int, state: IngestState, version: str) -> dict[str, Any]:
    vision = state.get("vision") or {}
    labels = state.get("labels") or {}
    return {
        "post_id": post_id,
        "ocr_text": vision.get("ocr_text") or None,
        "vlm_caption": vision.get("caption") or None,
        "topic_labels": [labels["topic"]] if labels else None,
        "is_ad": labels.get("is_ad"),
        "is_spoiler": labels.get("is_spoiler"),
        "quality_score": float(labels["quality"]) if labels else None,
        "injection_flag": state.get("injection_flag", False),
        "enriched_at": datetime.now(UTC),
        "model_version": version,
    }


async def run_ingest(
    factory: async_sessionmaker[AsyncSession],
    deps: IngestDeps,
    *,
    topic: str | None = None,
    limit: int | None = None,
    force: bool = False,
    concurrency: int = 2,
) -> IngestStats:
    version = model_version(deps)
    graph = build_ingest_graph(deps)
    stats = IngestStats(model_version=version)
    async with factory() as session:
        posts = await select_pending(session, version, topic=topic, limit=limit, force=force)
        inputs = [to_input(p, await album_text(session, p)) for p in posts]
    stats.selected = len(posts)
    sem = asyncio.Semaphore(concurrency)

    async def one(inp: IngestState) -> None:
        async with sem:
            try:
                state: IngestState = await graph.ainvoke(inp)
            except Exception as exc:  # a single post must never stop the run
                log.error("ingest_failed", post_id=inp["post_id"], error=repr(exc)[:300])
                stats.failed.append(inp["post_id"])
                return
            async with factory() as session:
                await session.execute(
                    insert_or_update(
                        session,
                        Enrichment.__table__,  # type: ignore[arg-type]
                        [to_row(inp["post_id"], state, version)],
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

    await asyncio.gather(*(one(i) for i in inputs))
    log.info("ingest_done", **{k: v for k, v in vars(stats).items() if k != "failed"})
    return stats
