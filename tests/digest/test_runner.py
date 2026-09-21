from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.digest.test_crew import CrewLLM
from tests.retrieval.conftest import FakeEmbedder
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Digest, Enrichment, Post
from tgdigest.digest.runner import make_digest, render_text, shown_before
from tgdigest.profile.runner import build_and_store_profile, record_feedback
from tgdigest.retrieval.index import PostIndex
from tgdigest.retrieval.runner import build_index

NOW = datetime.now(UTC)


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
        s.add_all(
            [
                Channel(id=1, username="memes", topic="humor"),
                Channel(id=2, username="memes2", topic="humor"),
                Channel(id=3, username="sf", topic="scifi"),
                Channel(id=4, username="films", topic="cinema"),
            ]
        )
        posts = {
            1: ("кот и дедлайн", 1, 1),
            2: ("кот спит", 1, 2),
            3: ("кот у ноутбука", 1, 3),
            4: ("понедельник опять", 2, 1),
            5: ("грустный кот", 2, 6),
            6: ("Лем и Стругацкие", 3, 1),
            7: ("новая книга Дукая", 3, 5),
            8: ("трейлер фильма", 4, 2),
            9: ("рецензия на фильм", 4, 6),
        }
        for pid, (text, ch, age) in posts.items():
            s.add(
                Post(
                    id=pid,
                    channel_id=ch,
                    tg_message_id=100 + pid,
                    posted_at=NOW - timedelta(days=age),
                    text=text,
                    views=100 * pid,
                    media_type="photo",
                    media_path="a.jpg",
                )
            )
            s.add(
                Enrichment(
                    post_id=pid,
                    topic_labels=["humor"],
                    is_ad=False,
                    quality_score=4.0,
                    model_version="v1",
                    ocr_text="ТЕКСТ" if pid == 5 else None,
                )
            )
        await s.commit()
    return f


async def test_make_digest_covers_topics_and_never_repeats(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    index = PostIndex(QdrantClient(":memory:"), FakeEmbedder())
    await build_index(factory, index)
    await record_feedback(factory, 1, [(1, "like")])
    await build_and_store_profile(factory, index, 1)
    llm: Any = CrewLLM()

    result, digest_id = await make_digest(factory, index, llm, 1, minutes=5, window_days=8)
    assert digest_id is not None and result.plan.items == 5
    topics = {it["topic"] for it in result.items}
    assert topics == {"humor", "scifi", "cinema"}  # every topic represented
    assert 1 not in {it["post_id"] for it in result.items}  # voted on → not recommended
    per_channel: dict[str, int] = {}
    for it in result.items:
        per_channel[it["channel"]] = per_channel.get(it["channel"], 0) + 1
    assert max(per_channel.values()) <= 2
    assert result.critic_iterations == 1 and "https://t.me/" in render_text(result)
    async with factory() as s:
        stored = (await s.execute(select(Digest))).scalar_one()
        assert stored.critic_iterations == 1 and len(stored.items_json) == len(result.items)
        assert await shown_before(s, 1) == {it["post_id"] for it in result.items}

    second, _ = await make_digest(factory, index, llm, 1, minutes=5, window_days=8)
    first_ids = {it["post_id"] for it in result.items}
    assert not first_ids & {it["post_id"] for it in second.items}  # nothing shown twice
    assert second.gaps  # the small corpus runs out: the shortfall is reported, not hidden
