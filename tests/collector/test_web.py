"""Web-preview source: parsing of captured pages and pagination against a mock transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tgdigest.collector.types import ChannelRef, FloodWait
from tgdigest.collector.web import (
    WebPreviewSource,
    html_to_text,
    parse_count,
    parse_page,
    stable_id,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def test_parse_count() -> None:
    assert parse_count("53") == 53
    assert parse_count("1.2K") == 1200
    assert parse_count("431K") == 431_000
    assert parse_count("3.4M") == 3_400_000
    assert parse_count("") is None and parse_count("n/a") is None


def test_albums_expand_to_members_with_text_on_the_first() -> None:
    info, msgs = parse_page(_fixture("horrorfantastcom"), "horrorfantastcom")
    assert info.id == 2291847929 and info.subscribers == 222
    assert info.title.startswith("Фантастика, хоррор")
    album = [m for m in msgs if m.grouped_id == 5254]
    assert [m.id for m in album] == list(range(5254, 5254 + len(album))) and len(album) >= 3
    assert album[0].text.startswith("Об этом графическом") and album[1].text == ""
    assert all(m.media_type == "photo" and m.media_tg_id and m.native["url"] for m in album)
    assert album[0].views == 53 and album[0].reactions_count == 2
    assert [m.id for m in msgs] == sorted(m.id for m in msgs)


def test_forwards_views_and_reactions() -> None:
    _, msgs = parse_page(_fixture("memehunter"), "memehunter")
    fwd = next(m for m in msgs if m.id == 17342)
    assert fwd.forward_from_msg_id == 675
    assert fwd.forward_from_channel == stable_id("asterixectomie")
    assert fwd.raw["forward_from_username"] == "asterixectomie"
    assert fwd.views == 1130 and fwd.reactions_count == 11
    assert fwd.forwards is None  # not exposed by the preview
    assert fwd.raw["source"] == "web_preview" and fwd.raw["reactions"]


def test_videos_get_thumbnails() -> None:
    _, msgs = parse_page(_fixture("luka_ebkov"), "luka_ebkov")
    videos = [m for m in msgs if m.media_type in {"video", "animation"}]
    assert videos and all(m.native["url"].startswith("https://cdn") for m in videos)
    assert all(m.media_ext == ".jpg" for m in videos)


def test_text_newlines_and_link_previews() -> None:
    info, msgs = parse_page(_fixture("lleodnevnik"), "lleodnevnik")
    assert info.subscribers == 9160
    poem = next(m for m in msgs if m.id == 2089)
    assert poem.text.startswith("#4етверостишки\n\nНичего требуют наши сердца!\n")
    assert poem.media_type is None and poem.raw["html_text"]
    assert {m.media_type for m in msgs if m.id > 2089} == {"webpage"}


def test_html_to_text_handles_none() -> None:
    assert html_to_text(None) == ""


# --- pagination against a synthetic server ------------------------------------------------

T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _block(username: str, mid: int, date: datetime) -> str:
    return (
        f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
        f'data-post="{username}/{mid}"><div class="tgme_widget_message_text">post {mid}</div>'
        f'<time datetime="{date.isoformat()}"></time></div></div>'
    )


class Server:
    """Serves ids 1..n_max, 20 per page, ``?before=`` like t.me/s; counts requests."""

    def __init__(self, username: str, n_max: int, fail_first: int = 0, status: int = 500) -> None:
        self.username, self.n_max, self.fail_first, self.status = (
            username,
            n_max,
            fail_first,
            status,
        )
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(self.status, headers={"Retry-After": "7"})
        if request.url.path.startswith("/file/"):
            return httpx.Response(200, content=b"\xff\xd8jpeg")
        before = request.url.params.get("before")
        hi = int(before) - 1 if before else self.n_max
        ids = [i for i in range(max(1, hi - 19), hi + 1)]
        body = "".join(_block(self.username, i, T0 + timedelta(hours=i)) for i in ids)
        return httpx.Response(200, text=f"<html><body>{body}</body></html>")


def _source(server: Server, sleeps: list[float]) -> WebPreviewSource:
    async def sleep(s: float) -> None:
        sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    return WebPreviewSource(client, delay_s=0.5, sleep=sleep)


def _setup(server: Server) -> tuple[WebPreviewSource, list[float]]:
    sleeps: list[float] = []
    return _source(server, sleeps), sleeps


async def test_first_pass_walks_back_to_since_and_yields_ascending() -> None:
    server = Server("ch", n_max=55)
    src, sleeps = _setup(server)
    got = [m async for m in src.iter_messages(ChannelRef(1, "ch"), since=T0 + timedelta(hours=30))]
    assert [m.id for m in got] == list(range(30, 56))
    assert len(server.requests) == 2  # 55..36, then 35..16 (boundary inside)
    assert "before=36" in server.requests[1]
    assert sleeps and all(s <= 0.5 for s in sleeps)  # politeness pause between requests


async def test_incremental_pass_stops_at_min_id() -> None:
    server = Server("ch", n_max=55)
    src, _sleeps = _setup(server)
    got = [m async for m in src.iter_messages(ChannelRef(1, "ch"), min_id=50)]
    assert [m.id for m in got] == [51, 52, 53, 54, 55] and len(server.requests) == 1


async def test_limit_keeps_the_oldest_after_the_cursor() -> None:
    server = Server("ch", n_max=55)
    src, _sleeps = _setup(server)
    got = [m async for m in src.iter_messages(ChannelRef(1, "ch"), min_id=40, limit=3)]
    assert [m.id for m in got] == [41, 42, 43]


async def test_walks_to_the_channel_start_without_looping() -> None:
    server = Server("ch", n_max=25)
    src, _sleeps = _setup(server)
    got = [m async for m in src.iter_messages(ChannelRef(1, "ch"), since=T0)]
    assert [m.id for m in got] == list(range(1, 26)) and len(server.requests) == 2


async def test_429_becomes_flood_wait_with_retry_after() -> None:
    server = Server("ch", 5, fail_first=1, status=429)
    src, _sleeps = _setup(server)
    with pytest.raises(FloodWait) as exc:
        await src.resolve_channel("ch")
    assert exc.value.seconds == 7


async def test_5xx_is_retried_then_succeeds() -> None:
    server = Server("ch", 5, fail_first=2, status=503)
    src, sleeps = _setup(server)
    info = await src.resolve_channel("ch")
    assert info.username == "ch" and len(server.requests) == 3 and 5.0 in sleeps


async def test_resolve_channel_without_posts_fails() -> None:
    server = Server("ch", 0)
    src, _sleeps = _setup(server)
    with pytest.raises(ValueError, match="no public preview"):
        await src.resolve_channel("ch")


async def test_download_media_writes_the_file(tmp_path: Path) -> None:
    server = Server("ch", 5)
    src, _sleeps = _setup(server)
    _, msgs = parse_page(_fixture("memehunter"), "memehunter")
    photo = next(m for m in msgs if m.media_type == "photo")
    dest = tmp_path / "x.jpg"
    saved = await src.download_media(photo, dest)
    assert saved == dest and dest.read_bytes().startswith(b"\xff\xd8")
