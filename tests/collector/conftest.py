from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tgdigest.collector.types import ChannelInfo, ChannelRef, FloodWait, RawMessage
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def msg(
    i: int,
    *,
    text: str = "",
    media_type: str | None = None,
    media_tg_id: int | None = None,
    grouped_id: int | None = None,
    fwd: tuple[int, int] | None = None,
) -> RawMessage:
    return RawMessage(
        id=i,
        date=T0 + timedelta(hours=i),
        text=text or f"post {i}",
        media_type=media_type,
        media_tg_id=media_tg_id,
        views=100 * i,
        forwards=i,
        reactions_count=2 * i,
        forward_from_channel=fwd[0] if fwd else None,
        forward_from_msg_id=fwd[1] if fwd else None,
        grouped_id=grouped_id,
        raw={"id": i, "bytes": "AQID"},
    )


class FakeSource:
    """In-memory Telegram: deterministic messages, optional FloodWait injection, fake downloads."""

    def __init__(self, messages: dict[int, list[RawMessage]] | None = None) -> None:
        self.messages: dict[int, list[RawMessage]] = messages or {}
        self.channels: dict[str, ChannelInfo] = {}
        self.flood_after: int | None = None  # raise FloodWait after yielding this many messages
        self.flood_seconds = 30
        self.downloads: list[int] = []
        self.fail_downloads: set[int] = set()
        self.resolve_calls = 0

    async def resolve_channel(self, username: str) -> ChannelInfo:
        self.resolve_calls += 1
        return self.channels[username]

    async def iter_messages(
        self,
        channel: ChannelRef,
        *,
        min_id: int = 0,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        yielded = 0
        for m in sorted(self.messages.get(channel.id, []), key=lambda m: m.id):
            if min_id and m.id <= min_id:
                continue
            if not min_id and since is not None and m.date < since:
                continue
            if limit is not None and yielded >= limit:
                return
            if self.flood_after is not None and yielded >= self.flood_after:
                self.flood_after = None  # only once
                raise FloodWait(self.flood_seconds)
            yielded += 1
            yield m

    async def download_media(self, message: RawMessage, dest: Path) -> Path | None:
        if message.media_tg_id in self.fail_downloads:
            raise OSError("boom")
        self.downloads.append(message.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8fake-jpeg")
        return dest


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(engine)


@pytest.fixture
async def channel(factory: async_sessionmaker[AsyncSession]) -> Channel:
    async with factory() as session:
        ch = Channel(id=1001, username="memes", title="Memes", topic="humor")
        session.add(ch)
        await session.commit()
        return ch


async def no_sleep(_: float) -> None:
    return None
