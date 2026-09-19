"""Incremental channel sync: sequential, idempotent, resumable after FloodWait (SPEC §6.1)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.collector.config import ChannelsConfig
from tgdigest.collector.media import MediaStore
from tgdigest.collector.types import ChannelRef, FloodWait, RawMessage, TelegramSource
from tgdigest.db.models import Channel, Post
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
