"""Incremental channel sync: sequential, idempotent, resumable after FloodWait (SPEC §6.1)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.collector.config import ChannelsConfig
from tgdigest.collector.media import MediaStore
from tgdigest.collector.types import ChannelRef, FloodWait, RawMessage, TelegramSource
from tgdigest.db.models import (
    Channel,
    Cluster,
    Enrichment,
    Feedback,
    Post,
    PostCluster,
    PostSignature,
)
from tgdigest.db.upsert import insert_ignore

log = structlog.get_logger(__name__)

DEFAULT_HISTORY = timedelta(days=183)  # SPEC §2.1: six months
Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class FloodWaitPolicy:
    """Sleep what Telegram asks, growing exponentially on repeats, sequential by design."""

    factor: float = 2.0
    cap_s: float = 3600.0
    max_waits: int = 8
    waits: int = 0

    def delay(self, requested_s: int) -> float:
        self.waits += 1
        if self.waits > self.max_waits:
            raise RuntimeError(f"gave up after {self.max_waits} flood waits")
        return min(requested_s * self.factor ** (self.waits - 1) + 1, self.cap_s)


@dataclass
class SyncStats:
    channel_id: int
    username: str
    fetched: int = 0
    inserted: int = 0
    media_downloaded: int = 0
    media_reused: int = 0
    flood_waits: int = 0
    last_message_id: int = 0
    errors: list[str] = field(default_factory=list)


def post_row(message: RawMessage, channel_id: int, media_path: str | None) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "tg_message_id": message.id,
        "posted_at": message.date,
        "text": message.text,
        "media_type": message.media_type,
        "media_path": media_path,
        "media_tg_id": message.media_tg_id,
        "views": message.views,
        "forwards": message.forwards,
        "reactions_count": message.reactions_count,
        "forward_from_channel": message.forward_from_channel,
        "forward_from_msg_id": message.forward_from_msg_id,
        "grouped_id": message.grouped_id,
        "raw_json": message.raw,
    }


async def _flush(
    session: AsyncSession, channel_id: int, rows: list[dict[str, Any]], stats: SyncStats
) -> None:
    if not rows:
        return
    result = await session.execute(insert_ignore(session, Post.__table__, rows))  # type: ignore[arg-type]
    inserted = getattr(result, "rowcount", -1)  # CursorResult has it; the stub type does not
    stats.inserted += inserted if inserted >= 0 else 0
    stats.last_message_id = max(stats.last_message_id, max(r["tg_message_id"] for r in rows))
    await session.execute(
        update(Channel)
        .where(Channel.id == channel_id)
        .values(last_message_id=stats.last_message_id, last_synced_at=datetime.now(UTC))
    )
    await session.commit()  # a crash after this point resumes from last_message_id
    rows.clear()


async def sync_channel(
    session_factory: async_sessionmaker[AsyncSession],
    source: TelegramSource,
    media: MediaStore | None,
    channel_id: int,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    batch_size: int = 100,
    sleep: Sleeper = asyncio.sleep,
    policy: FloodWaitPolicy | None = None,
) -> SyncStats:
    policy = policy or FloodWaitPolicy()
    async with session_factory() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"unknown channel id {channel_id}")
        stats = SyncStats(channel.id, channel.username, last_message_id=channel.last_message_id)
        since = since or (datetime.now(UTC) - DEFAULT_HISTORY)
        clog = log.bind(channel=channel.username)

        while True:
            rows: list[dict[str, Any]] = []
            try:
                async for message in source.iter_messages(
                    ChannelRef(channel.id, channel.username),
                    min_id=stats.last_message_id,
                    since=since,
                    limit=limit,
                ):
                    stats.fetched += 1
                    media_path = None
                    if media is not None and message.wants_download:
                        try:
                            media_path, reused = await media.fetch(source, message)
                        except FloodWait:
                            raise
                        except Exception as exc:  # a broken file must not stop the channel
                            stats.errors.append(f"media {message.id}: {exc!r}")
                            clog.warning("media_failed", message_id=message.id, error=repr(exc))
                            reused = False
                        if media_path is not None:
                            if reused:
                                stats.media_reused += 1
                            else:
                                stats.media_downloaded += 1
                    rows.append(post_row(message, channel_id, media_path))
                    if len(rows) >= batch_size:
                        await _flush(session, channel_id, rows, stats)
                await _flush(session, channel_id, rows, stats)
                break
            except FloodWait as exc:
                await _flush(session, channel_id, rows, stats)
                stats.flood_waits += 1
                delay = policy.delay(exc.seconds)
                clog.warning("flood_wait", requested_s=exc.seconds, sleeping_s=delay)
                await sleep(delay)
        clog.info("synced", **{k: v for k, v in vars(stats).items() if k != "errors"})
        return stats


@dataclass
class RefreshStats:
    channel_id: int
    username: str
    fetched: int = 0
    dates_fixed: int = 0
    texts_fixed: int = 0
    enrichment_reset: int = 0
    """Enrichment rows dropped because the text they were computed from changed."""
    counters_updated: int = 0
    flood_waits: int = 0


async def refresh_channel(
    session_factory: async_sessionmaker[AsyncSession],
    source: TelegramSource,
    channel_id: int,
    *,
    since: datetime | None = None,
    sleep: Sleeper = asyncio.sleep,
    policy: FloodWaitPolicy | None = None,
) -> RefreshStats:
    """Re-read every message in the window and refresh the rows we hold: ``posted_at`` and
    ``text`` (repairs rows an older parser got wrong), views, forwards, reactions. No inserts,
    no media, idempotent."""
    policy = policy or FloodWaitPolicy()
    async with session_factory() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"unknown channel id {channel_id}")
        stats = RefreshStats(channel.id, channel.username)
        since = since or (datetime.now(UTC) - DEFAULT_HISTORY)
        clog = log.bind(channel=channel.username)
        stmt = select(
            Post.tg_message_id, Post.posted_at, Post.views, Post.forwards, Post.text
        ).where(Post.channel_id == channel_id)
        held = {
            mid: (at, v, f, text) for mid, at, v, f, text in (await session.execute(stmt)).all()
        }

        changed_text: list[int] = []
        while True:
            try:
                async for message in source.iter_messages(
                    ChannelRef(channel.id, channel.username), min_id=0, since=since
                ):
                    stats.fetched += 1
                    current = held.get(message.id)
                    if current is None:
                        continue
                    posted_at, views, forwards, text = current
                    if posted_at.tzinfo is None:  # SQLite hands back naive UTC
                        posted_at = posted_at.replace(tzinfo=UTC)
                    values: dict[str, Any] = {}
                    if abs((posted_at - message.date).total_seconds()) > 60:
                        values["posted_at"] = message.date
                        stats.dates_fixed += 1
                    if message.text != text:
                        values.update(text=message.text, raw_json=message.raw)
                        stats.texts_fixed += 1
                        changed_text.append(message.id)
                    if (message.views, message.forwards) != (views, forwards):
                        values.update(views=message.views, forwards=message.forwards)
                        values["reactions_count"] = message.reactions_count
                        stats.counters_updated += 1
                    if values:
                        await session.execute(
                            update(Post)
                            .where(Post.channel_id == channel_id)
                            .where(Post.tg_message_id == message.id)
                            .values(**values)
                        )
                        held[message.id] = (
                            message.date,
                            message.views,
                            message.forwards,
                            message.text,
                        )
                await session.commit()
                break
            except FloodWait as exc:
                await session.commit()
                stats.flood_waits += 1
                delay = policy.delay(exc.seconds)
                clog.warning("flood_wait", requested_s=exc.seconds, sleeping_s=delay)
                await sleep(delay)
        if changed_text:  # labels computed from the old text are stale: let ingest redo them
            stale = select(Post.id).where(
                Post.channel_id == channel_id, Post.tg_message_id.in_(changed_text)
            )
            result = await session.execute(delete(Enrichment).where(Enrichment.post_id.in_(stale)))
            stats.enrichment_reset = int(getattr(result, "rowcount", 0) or 0)
            await session.commit()
        clog.info("refreshed", **vars(stats))
        return stats


async def upsert_channels(
    session_factory: async_sessionmaker[AsyncSession],
    source: TelegramSource,
    config: ChannelsConfig,
    *,
    sleep: Sleeper = asyncio.sleep,
) -> list[Channel]:
    """Resolve every channel in the YAML and insert/refresh its row (topic, title, subscribers)."""
    policy = FloodWaitPolicy()
    out: list[Channel] = []
    async with session_factory() as session:
        for topic, spec in config.flat():
            while True:
                try:
                    info = await source.resolve_channel(spec.username)
                    break
                except FloodWait as exc:
                    await sleep(policy.delay(exc.seconds))
            channel = await session.get(Channel, info.id)
            if channel is None:
                channel = Channel(
                    id=info.id, username=info.username, topic=topic, source=spec.source
                )
                session.add(channel)
            channel.username = info.username
            channel.title = info.title
            channel.topic = topic
            channel.subscribers = info.subscribers
            channel.is_active = True
            out.append(channel)
        await session.commit()
    return out


@dataclass
class RemovalStats:
    """What leaving the corpus costs: rows gone, plus the media files nothing else references."""

    channel_id: int
    username: str
    posts: int = 0
    enrichment: int = 0
    feedback: int = 0
    clusters_touched: int = 0
    clusters_removed: int = 0
    post_ids: list[int] = field(default_factory=list)
    media_files: list[str] = field(default_factory=list)


async def remove_channel(
    session_factory: async_sessionmaker[AsyncSession], username: str
) -> RemovalStats | None:
    """Delete a channel and everything hanging off it (SPEC §2.1: the YAML is the corpus).

    Rows are deleted explicitly in dependency order rather than left to ``ON DELETE CASCADE``,
    so the result does not depend on the backend's foreign-key enforcement; the caller removes
    the returned media files and index points. Media is shared across channels by
    ``media_tg_id``, so only files no remaining post references are listed.
    """
    async with session_factory() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.username == username))
        ).scalar_one_or_none()
        if channel is None:
            return None
        stats = RemovalStats(channel_id=channel.id, username=channel.username or username)
        stats.post_ids = [
            int(i)
            for (i,) in (
                await session.execute(select(Post.id).where(Post.channel_id == channel.id))
            ).all()
        ]
        stats.posts = len(stats.post_ids)
        if stats.post_ids:
            stats.enrichment = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Enrichment)
                        .where(Enrichment.post_id.in_(stats.post_ids))
                    )
                ).scalar_one()
            )
            stats.feedback = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Feedback)
                        .where(Feedback.post_id.in_(stats.post_ids))
                    )
                ).scalar_one()
            )
            stats.clusters_touched = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(PostCluster.cluster_id))).where(
                            PostCluster.post_id.in_(stats.post_ids)
                        )
                    )
                ).scalar_one()
            )
            mine = (
                await session.execute(
                    select(Post.media_path)
                    .where(Post.channel_id == channel.id, Post.media_path.is_not(None))
                    .distinct()
                )
            ).all()
            shared = {
                path
                for (path,) in (
                    await session.execute(
                        select(Post.media_path)
                        .where(
                            Post.channel_id != channel.id,
                            Post.media_path.in_([p for (p,) in mine]),
                        )
                        .distinct()
                    )
                ).all()
            }
            stats.media_files = [str(p) for (p,) in mine if p not in shared]
            for table, column in (
                (Feedback, Feedback.post_id),
                (PostCluster, PostCluster.post_id),
                (PostSignature, PostSignature.post_id),
                (Enrichment, Enrichment.post_id),
            ):
                await session.execute(delete(table).where(column.in_(stats.post_ids)))
            await session.execute(
                update(Cluster)
                .where(Cluster.representative_post_id.in_(stats.post_ids))
                .values(representative_post_id=None)
            )
            await session.execute(delete(Post).where(Post.channel_id == channel.id))
        await session.execute(delete(Channel).where(Channel.id == channel.id))
        empty = [  # clusters the delete emptied
            int(i)
            for (i,) in (
                await session.execute(
                    select(Cluster.id).where(
                        ~Cluster.id.in_(select(PostCluster.cluster_id).distinct())
                    )
                )
            ).all()
        ]
        if empty:
            await session.execute(delete(Cluster).where(Cluster.id.in_(empty)))
        stats.clusters_removed = len(empty)
        await session.commit()
    log.info(
        "channel_removed",
        channel=stats.username,
        posts=stats.posts,
        enrichment=stats.enrichment,
        feedback=stats.feedback,
        clusters_touched=stats.clusters_touched,
        clusters_removed=stats.clusters_removed,
        media_files=len(stats.media_files),
    )
    return stats


async def link_forwards(session: AsyncSession) -> int:
    """Point ``forward_from_channel`` at the corpus channel when the source is one of ours.

    The web preview only names the source (``raw_json.forward_from_username``); the collector
    stores a synthetic id for it. Once the corpus is known, forwards from corpus channels get
    the real id so cluster links (SPEC §6.3 step 5) can join on it.
    """
    from sqlalchemy import func

    lowered = func.lower(Post.raw_json["forward_from_username"].as_string())
    subq = select(Channel.id).where(func.lower(Channel.username) == lowered).scalar_subquery()
    stmt = (
        update(Post)
        .where(Post.forward_from_channel.is_not(None))
        .where(lowered.in_(select(func.lower(Channel.username))))
        .where(Post.forward_from_channel != subq)
        .values(forward_from_channel=subq)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def channel_stats(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(
            Channel.topic,
            Channel.username,
            Channel.last_message_id,
            func.count(Post.id).label("posts"),
            func.count(Post.media_path).label("with_media"),
            func.min(Post.posted_at).label("first"),
            func.max(Post.posted_at).label("last"),
        )
        .outerjoin(Post, Post.channel_id == Channel.id)
        .group_by(Channel.id)
        .order_by(Channel.topic, Channel.username)
    )
    return [dict(r._mapping) for r in (await session.execute(stmt)).all()]
