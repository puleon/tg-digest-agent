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
from tgdigest.ingest.graph import (
    IngestDeps,
    IngestState,
    Member,
    build_ingest_graph,
    model_version,
)

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


async def select_pending(
    session: AsyncSession,
    version: str,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    force: bool = False,
) -> list[Post]:
    """Newest first, so a bounded run always covers the freshest posts; ``since`` limits the
    history depth (the first version of the system needs three months, not six)."""
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
    concurrency: int = 2,
) -> IngestStats:
    version = model_version(deps)
    graph = build_ingest_graph(deps)
    stats = IngestStats(model_version=version)
    async with factory() as session:
        posts = await select_pending(
            session, version, topic=topic, since=since, limit=limit, force=force
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

    await asyncio.gather(*(one(i) for i in inputs))
    log.info("ingest_done", **{k: v for k, v in vars(stats).items() if k != "failed"})
    return stats
