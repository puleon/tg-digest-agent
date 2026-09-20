from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.collector.conftest import T0
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Cluster, Post, PostCluster, PostSignature
from tgdigest.dedup.runner import compute_signatures, rebuild_clusters


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Path]]:
    engine: AsyncEngine = make_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    media = tmp_path / "media"
    (media / "a").mkdir(parents=True)
    img = Image.new("RGB", (64, 64), (200, 30, 30))  # a flat image would be treated as blank
    img.paste((20, 200, 40), (8, 8, 40, 40))
    img.save(media / "a" / "1.jpg")
    (media / "a" / "2.jpg").write_bytes((media / "a" / "1.jpg").read_bytes())  # byte-identical
    f = make_session_factory(engine)
    async with f() as s:
        s.add_all(
            [Channel(id=1, username="a", topic="humor"), Channel(id=2, username="b", topic="humor")]
        )
        s.add_all(
            [
                Post(
                    id=1,
                    channel_id=1,
                    tg_message_id=1,
                    posted_at=T0,
                    text="Тот самый мем про дедлайн",
                    media_type="photo",
                    media_path="a/1.jpg",
                ),
                Post(
                    id=2,
                    channel_id=2,
                    tg_message_id=7,
                    posted_at=T0,
                    text="",
                    media_type="photo",
                    media_path="a/2.jpg",
                ),
                Post(
                    id=3,
                    channel_id=2,
                    tg_message_id=8,
                    posted_at=T0,
                    text="Совсем другой пост о кино",
                ),
            ]
        )
        await s.commit()
    yield f, media
    await engine.dispose()


async def test_signatures_then_clusters_are_idempotent(
    factory: tuple[async_sessionmaker[AsyncSession], Path],
) -> None:
    f, media = factory
    stats = await compute_signatures(f, media)
    assert (stats.selected, stats.computed, stats.with_file, stats.with_phash) == (3, 3, 2, 2)
    again = await compute_signatures(f, media)
    assert again.selected == 0

    res = await rebuild_clusters(f)
    assert res.clusters == 1 and res.clustered_posts == 2 and res.stage_counts["file"] == 1
    res2 = await rebuild_clusters(f)  # wholesale rebuild: same result, no leftovers
    async with f() as s:
        clusters = (await s.execute(select(Cluster))).scalars().all()
        members = (await s.execute(select(PostCluster))).scalars().all()
        sigs = (await s.execute(select(PostSignature))).scalars().all()
    assert len(clusters) == 1 and clusters[0].representative_post_id == 1 and clusters[0].size == 2
    assert sorted(m.post_id for m in members) == [1, 2] and {m.stage for m in members} == {"file"}
    assert len(sigs) == 3 and res2.clusters == 1
