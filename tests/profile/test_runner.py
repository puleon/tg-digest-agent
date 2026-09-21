from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.retrieval.conftest import FakeEmbedder
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.profile.runner import (
    build_and_store_profile,
    load_profile,
    load_votes,
    onboarding_sample,
    rank_candidates,
    record_feedback,
)
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
        texts = {
            1: ("кот и дедлайн", 1, 900),
            2: ("кот спит на клавиатуре", 1, 800),
            3: ("понедельник опять", 2, 50),
            4: ("кот у ноутбука", 2, 60),
            5: ("Лем и Стругацкие", 3, 300),
            6: ("новая книга Дукая", 3, 310),
            7: ("трейлер фильма", 4, 5000),
            8: ("рецензия на фильм", 4, 4000),
            9: ("купи курс по нейросетям", 4, 99999),
        }
        for pid, (text, ch, views) in texts.items():
            s.add(
                Post(
                    id=pid,
                    channel_id=ch,
                    tg_message_id=pid,
                    posted_at=NOW - timedelta(days=pid % 5),
                    text=text,
                    views=views,
                    media_type="photo",
                    media_path="a.jpg",
                )
            )
            s.add(
                Enrichment(
                    post_id=pid,
                    topic_labels=["humor"],
                    is_ad=(pid == 9),
                    quality_score=4.0,
                    model_version="v1",
                )
            )
        await s.commit()
    return f


@pytest.fixture
async def index(factory: async_sessionmaker[AsyncSession]) -> PostIndex:
    idx = PostIndex(QdrantClient(":memory:"), FakeEmbedder())
    await build_index(factory, idx)
    return idx


async def test_onboarding_sample_is_stratified_and_skips_ads(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        posts = await onboarding_sample(s, per_topic=2, max_per_channel=1, seed=1)
    assert len(posts) == 4  # humor: two channels → 2; scifi and cinema: one channel each → 1
    assert all(p.post_id != 9 for p in posts)  # the ad never reaches the sheet
    by_topic: dict[str, list[str]] = {}
    for p in posts:
        by_topic.setdefault(p.topic, []).append(p.channel)
    assert len(by_topic["humor"]) == 2 and len(set(by_topic["humor"])) == 2
    assert [p.topic for p in posts] == sorted(p.topic for p in posts)


async def test_feedback_profile_and_ranking_end_to_end(
    factory: async_sessionmaker[AsyncSession], index: PostIndex
) -> None:
    n = await record_feedback(factory, 7, [(1, "like"), (2, "like"), (5, "dislike"), (3, "bogus")])
    assert n == 3
    await record_feedback(factory, 7, [(5, "like")], context="search", query="Лем")  # changed mind
    async with factory() as s:
        votes = {v.post_id: v.signal for v in await load_votes(s, 7)}
    assert votes == {1: "like", 2: "like", 5: "like"}

    profile = await build_and_store_profile(factory, index, 7)
    assert profile.votes == 3 and profile.like_rate == 1.0
    assert profile.topic_weights["humor"] > profile.topic_weights["cinema"]
    assert len(profile.interest_centroids) == 1 and len(profile.interest_centroids[0]) == 1024
    async with factory() as s:
        stored = await load_profile(s, 7)
    assert stored is not None and stored.like_rate == 1.0 and stored.votes == 3
    assert stored.channel_affinity[1] > stored.channel_affinity.get(4, 0)

    ranked = await rank_candidates(factory, index, 7, days=10, limit=10)
    ids = [r["post_id"] for r in ranked]
    assert not {1, 2, 5} & set(ids)  # already voted on → never recommended again
    assert ids[0] == 4  # "кот у ноутбука": nearest to the liked cats, humor
    assert 9 not in ids  # ads are not candidates
    baseline = await rank_candidates(factory, index, 7, days=10, limit=10, baseline=True)
    assert baseline[0]["post_id"] == 7  # views win the naive baseline
    assert await rank_candidates(factory, index, 99, days=10, limit=3)  # no profile: still ranks
