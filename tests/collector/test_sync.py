from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.collector.conftest import T0, FakeSource, msg, no_sleep
from tgdigest.collector.media import MediaStore
from tgdigest.collector.sync import FloodWaitPolicy, sync_channel
from tgdigest.db.models import Channel, Post


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as s:
        return (await s.execute(select(func.count(Post.id)))).scalar_one()


async def _cursor(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as s:
        return (await s.get_one(Channel, 1001)).last_message_id


async def test_sync_inserts_posts_and_advances_cursor(
    factory: async_sessionmaker[AsyncSession], channel: Channel, tmp_path: Path
) -> None:
    src = FakeSource({1001: [msg(1), msg(2, media_type="photo", media_tg_id=777), msg(3)]})
    stats = await sync_channel(factory, src, MediaStore(tmp_path), 1001, since=T0)

    assert (stats.fetched, stats.inserted, stats.media_downloaded) == (3, 3, 1)
    assert await _count(factory) == 3
    assert await _cursor(factory) == 3
    async with factory() as s:
        post = (await s.execute(select(Post).where(Post.tg_message_id == 2))).scalar_one()
    assert post.media_path == "777/777.jpg"
    assert (tmp_path / post.media_path).exists()
    assert post.raw_json == {"id": 2, "bytes": "AQID"}


async def test_rerun_is_idempotent_and_incremental(
    factory: async_sessionmaker[AsyncSession], channel: Channel
) -> None:
    src = FakeSource({1001: [msg(1), msg(2)]})
    await sync_channel(factory, src, None, 1001, since=T0)
    again = await sync_channel(factory, src, None, 1001, since=T0)
    assert again.fetched == 0 and again.inserted == 0
    assert await _count(factory) == 2

    src.messages[1001] += [msg(3), msg(4)]
    third = await sync_channel(factory, src, None, 1001, since=T0)
    assert (third.fetched, third.inserted) == (2, 2)
    assert await _count(factory) == 4
    assert await _cursor(factory) == 4


async def test_since_bounds_the_first_sync_only(
    factory: async_sessionmaker[AsyncSession], channel: Channel
) -> None:
    src = FakeSource({1001: [msg(1), msg(2), msg(3)]})  # msg(i) is posted at T0 + i hours
    stats = await sync_channel(factory, src, None, 1001, since=T0 + timedelta(hours=2))
    assert stats.fetched == 2 and await _cursor(factory) == 3


async def test_flood_wait_resumes_from_committed_cursor_without_duplicates(
    factory: async_sessionmaker[AsyncSession], channel: Channel
) -> None:
    src = FakeSource({1001: [msg(i) for i in range(1, 7)]})
    src.flood_after, src.flood_seconds = 3, 30
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    stats = await sync_channel(factory, src, None, 1001, since=T0, batch_size=2, sleep=sleep)
    assert stats.flood_waits == 1 and sleeps == [31.0]
    assert stats.inserted == 6 and await _count(factory) == 6
    assert await _cursor(factory) == 6


def test_flood_wait_policy_grows_exponentially_and_gives_up() -> None:
    p = FloodWaitPolicy(factor=2.0, cap_s=100.0, max_waits=4)
    assert [p.delay(10) for _ in range(4)] == [11.0, 21.0, 41.0, 81.0]
    with pytest.raises(RuntimeError, match="gave up"):
        p.delay(10)
    assert FloodWaitPolicy(cap_s=50.0).delay(3600) == 50.0


async def test_media_is_downloaded_once_and_reused_across_channels(
    factory: async_sessionmaker[AsyncSession], channel: Channel, tmp_path: Path
) -> None:
    async with factory() as s:
        s.add(Channel(id=1002, username="cinema_stills", topic="cinema"))
        await s.commit()
    same = dict(media_type="photo", media_tg_id=4242)
    src = FakeSource({1001: [msg(1, **same)], 1002: [msg(5, **same), msg(6, **same)]})  # type: ignore[arg-type]
    store = MediaStore(tmp_path)

    a = await sync_channel(factory, src, store, 1001, since=T0)
    b = await sync_channel(factory, src, store, 1002, since=T0)
    assert (a.media_downloaded, a.media_reused) == (1, 0)
    assert (b.media_downloaded, b.media_reused) == (0, 2)
    assert src.downloads == [1]
    async with factory() as s:
        paths = set((await s.execute(select(Post.media_path))).scalars())
    assert paths == {"242/4242.jpg"}


async def test_video_gets_a_thumbnail_and_documents_do_not(
    factory: async_sessionmaker[AsyncSession], channel: Channel, tmp_path: Path
) -> None:
    src = FakeSource(
        {
            1001: [
                msg(1, media_type="video", media_tg_id=1),
                msg(2, media_type="document", media_tg_id=2),
                msg(3, media_type="webpage"),
            ]
        }
    )
    stats = await sync_channel(factory, src, MediaStore(tmp_path), 1001, since=T0)
    assert stats.media_downloaded == 1 and src.downloads == [1]
    async with factory() as s:
        rows = {p.tg_message_id: p for p in (await s.execute(select(Post))).scalars()}
    assert rows[1].media_path == "001/1.jpg" and rows[2].media_path is None
    assert rows[3].media_type == "webpage" and rows[3].media_path is None


async def test_media_failure_does_not_stop_the_channel(
    factory: async_sessionmaker[AsyncSession], channel: Channel, tmp_path: Path
) -> None:
    src = FakeSource({1001: [msg(1, media_type="photo", media_tg_id=9), msg(2)]})
    src.fail_downloads = {9}
    stats = await sync_channel(factory, src, MediaStore(tmp_path), 1001, since=T0)
    assert stats.inserted == 2 and stats.media_downloaded == 0
    assert stats.errors and "boom" in stats.errors[0]


async def test_albums_and_forwards_are_preserved(
    factory: async_sessionmaker[AsyncSession], channel: Channel
) -> None:
    src = FakeSource(
        {
            1001: [
                msg(1, grouped_id=555, media_type="photo", media_tg_id=1),
                msg(2, grouped_id=555, media_type="photo", media_tg_id=2),
                msg(3, fwd=(2002, 17)),
            ]
        }
    )
    await sync_channel(factory, src, None, 1001, since=T0)
    async with factory() as s:
        rows = {p.tg_message_id: p for p in (await s.execute(select(Post))).scalars()}
    assert rows[1].grouped_id == rows[2].grouped_id == 555
    assert (rows[3].forward_from_channel, rows[3].forward_from_msg_id) == (2002, 17)
    assert rows[1].media_path is None  # media store was not given -> metadata only


async def test_unknown_channel_is_an_error(factory: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(ValueError, match="unknown channel"):
        await sync_channel(factory, FakeSource(), None, 404, sleep=no_sleep)
