"""Runs against the real Postgres (``make test-int`` on the box); skipped when unreachable."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.collector.conftest import T0, FakeSource, msg
from tgdigest.collector.sync import sync_channel
from tgdigest.config import Settings
from tgdigest.db.base import make_engine, make_session_factory
from tgdigest.db.models import Channel, Post

pytestmark = pytest.mark.integration

TEST_CHANNEL_ID = 999_000_001


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(Settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"postgres not reachable: {exc!r}")
    factory = make_session_factory(engine)
    async with factory() as s:
        await s.execute(delete(Channel).where(Channel.id == TEST_CHANNEL_ID))
        s.add(Channel(id=TEST_CHANNEL_ID, username="__it_test__", topic="humor"))
        await s.commit()
    yield factory
    async with factory() as s:
        await s.execute(delete(Channel).where(Channel.id == TEST_CHANNEL_ID))
        await s.commit()
    await engine.dispose()


async def test_insert_ignore_is_idempotent_on_postgres(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    src = FakeSource({TEST_CHANNEL_ID: [msg(1, text="привет"), msg(2, grouped_id=7)]})
    first = await sync_channel(pg_factory, src, None, TEST_CHANNEL_ID, since=T0)
    second = await sync_channel(pg_factory, src, None, TEST_CHANNEL_ID, since=T0)
    assert (first.inserted, second.inserted) == (2, 0)

    # cursor reset must not create duplicates either: the unique constraint is the last line
    async with pg_factory() as s:
        ch = await s.get_one(Channel, TEST_CHANNEL_ID)
        ch.last_message_id = 0
        await s.commit()
    third = await sync_channel(pg_factory, src, None, TEST_CHANNEL_ID, since=T0)
    assert third.fetched == 2 and third.inserted == 0

    async with pg_factory() as s:
        n = (
            await s.execute(select(func.count(Post.id)).where(Post.channel_id == TEST_CHANNEL_ID))
        ).scalar_one()
        raw = (await s.execute(select(Post.raw_json).where(Post.tg_message_id == 1))).scalar_one()
    assert n == 2 and raw == {"id": 1, "bytes": "AQID"}  # JSONB round trip
