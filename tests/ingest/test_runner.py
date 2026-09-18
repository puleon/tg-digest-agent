from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.collector.conftest import T0
from tests.ingest.conftest import FakeLLM
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.ingest.graph import IngestDeps
from tgdigest.ingest.runner import run_ingest


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    f = make_session_factory(engine)
    async with f() as s:
        s.add(Channel(id=1, username="memes", topic="humor"))
        s.add(Channel(id=2, username="sf", topic="scifi"))
        s.add_all(
            [
                Post(
                    id=10,
                    channel_id=1,
                    tg_message_id=1,
                    posted_at=T0,
                    text="",
                    grouped_id=5,
                    media_type="photo",
                    media_path="001/1.jpg",
                ),
                Post(
                    id=11,
                    channel_id=1,
                    tg_message_id=2,
                    posted_at=T0,
                    text="подпись альбома",
                    grouped_id=5,
                    media_type="photo",
                    media_path="001/1.jpg",
                ),
                Post(id=12, channel_id=2, tg_message_id=3, posted_at=T0, text="Лем и Стругацкие"),
            ]
        )
        await s.commit()
    return f


async def test_run_enriches_pending_posts_idempotently(
    factory: async_sessionmaker[AsyncSession], deps: IngestDeps, fake_llm: FakeLLM
) -> None:
    first = await run_ingest(factory, deps, concurrency=2)
    assert (first.selected, first.processed, first.failed) == (3, 3, [])
    assert first.prompt_tokens == 10 * 5  # 2 vision + 3 classify calls

    async with factory() as s:
        rows = {e.post_id: e for e in (await s.execute(select(Enrichment))).scalars()}
    assert set(rows) == {10, 11, 12}
    assert rows[10].ocr_text == "КОГДА ДЕДЛАЙН" and rows[12].ocr_text is None
    assert rows[12].topic_labels == ["humor"] and rows[12].model_version == first.model_version

    # the album caption reached the text-less member of the group
    album_call = next(
        c
        for c in fake_llm.calls
        if c["schema"] == "PostLabels" and "подпись альбома" in c["messages"][-1]["content"]
    )
    assert album_call

    calls_before = len(fake_llm.calls)
    second = await run_ingest(factory, deps)
    assert second.selected == 0 and len(fake_llm.calls) == calls_before

    forced = await run_ingest(factory, deps, topic="scifi", force=True)
    assert forced.selected == 1 and forced.processed == 1
    async with factory() as s:
        assert (
            len((await s.execute(select(Enrichment))).scalars().all()) == 3
        )  # updated, not duplicated


async def test_limit_and_topic_filters(
    factory: async_sessionmaker[AsyncSession], deps: IngestDeps
) -> None:
    stats = await run_ingest(factory, deps, topic="humor", limit=1)
    assert stats.selected == 1 and stats.processed == 1
