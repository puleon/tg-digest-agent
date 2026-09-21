from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.collector.conftest import T0
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Cluster, Enrichment, Post, PostCluster
from tgdigest.retrieval.index import PostIndex, SearchFilters
from tgdigest.retrieval.runner import build_index, load_facts


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
                    text="подпись альбома",
                    grouped_id=5,
                    media_type="photo",
                    media_path="a.jpg",
                ),
                Post(
                    id=11,
                    channel_id=1,
                    tg_message_id=2,
                    posted_at=T0,
                    text="",
                    grouped_id=5,
                    media_type="photo",
                    media_path="b.jpg",
                ),
                Post(id=12, channel_id=2, tg_message_id=3, posted_at=T0, text="Лем и Стругацкие"),
                Post(
                    id=13,
                    channel_id=2,
                    tg_message_id=4,
                    posted_at=T0,
                    text="Лем и Стругацкие (репост)",
                ),
            ]
        )
        s.add_all(
            [
                Enrichment(
                    post_id=10,
                    ocr_text="ПЕРВАЯ",
                    vlm_caption="кот",
                    topic_labels=["humor"],
                    is_ad=False,
                    quality_score=0.7,
                    model_version="v1",
                ),
                Enrichment(post_id=11, ocr_text="ВТОРАЯ", vlm_caption="пёс", model_version="v1"),
            ]
        )
        s.add(Cluster(id=1, topic="scifi", representative_post_id=12, first_seen_at=T0, size=2))
        s.add_all(
            [
                PostCluster(post_id=12, cluster_id=1, stage="text"),
                PostCluster(post_id=13, cluster_id=1, stage="text"),
            ]
        )
        await s.commit()
    return f


async def test_load_facts_groups_albums_and_marks_representatives(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        facts = {f.post_id: f for f in await load_facts(s)}
    assert set(facts) == {10, 12, 13}  # 11 folded into album 10
    album = facts[10]
    assert [m.ocr_text for m in album.members] == ["ПЕРВАЯ", "ВТОРАЯ"]
    assert album.label == "humor" and album.quality == 0.7 and album.is_ad is False
    assert facts[12].is_representative and not facts[13].is_representative
    assert facts[13].cluster_id == 1 and facts[10].cluster_id is None
    async with factory() as s:
        assert [f.post_id for f in await load_facts(s, topic="scifi")] == [12, 13]


async def test_build_index_end_to_end_is_idempotent(
    factory: async_sessionmaker[AsyncSession], index: PostIndex
) -> None:
    stats = await build_index(factory, index)
    assert (stats.posts, stats.indexed, stats.with_enrichment) == (3, 3, 1)
    assert stats.by_source == {"ocr": 1, "caption": 1}
    again = await build_index(factory, index)
    assert (again.indexed, again.unchanged) == (0, 3)
    hits = index.search("ВТОРАЯ", variant="text_ocr", mode="sparse")
    assert [h.post_id for h in hits] == [10]  # member OCR is searchable under the album's post
    deduped = index.search(
        "Лем", variant="text", mode="sparse", filters=SearchFilters(representatives_only=True)
    )
    assert [h.post_id for h in deduped] == [12]
