"""Telegram's public web preview (``https://t.me/s/<channel>``) as a message source.

No account, no API keys: the preview is Telegram's own HTML rendering of a public channel,
paginated with ``?before=<message_id>`` (20 posts per page). Compared with MTProto it lacks the
forward counter and rounds views on big channels; everything the pipeline needs — text, ids,
dates, photos in full resolution, video thumbnails, albums, "forwarded from", reactions,
subscriber count and the numeric channel id — is there. The markup is the contract: parsing is
covered by fixture tests, and every unknown block degrades to ``media_type="other"``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from selectolax.parser import HTMLParser, Node

from tgdigest.collector.types import ChannelInfo, ChannelRef, FloodWait, RawMessage

log = structlog.get_logger(__name__)

BASE_URL = "https://t.me/s/{username}"
USER_AGENT = "tg-digest-agent/0.1 (+https://github.com/puleon/tg-digest-agent; personal index)"
PAGE_SIZE = 20
_POST_RE = re.compile(r"^([A-Za-z0-9_]+)/(\d+)$")
_SINGLE_RE = re.compile(r"/(\d+)(?:\?single)?$")
_BG_URL_RE = re.compile(r"background-image:\s*url\('([^']+)'\)")
_TME_RE = re.compile(r"^https?://t\.me/([A-Za-z0-9_]+)(?:/(\d+))?")
_COUNT_SUFFIX = {"K": 1_000, "M": 1_000_000}
_INT63 = (1 << 63) - 1
Sleeper = Callable[[float], Awaitable[None]]


def stable_id(text: str) -> int:
    """Deterministic positive int63 from a string (media URL, external channel name)."""
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "big") & _INT63


def parse_count(text: str | None) -> int | None:
    """'1.2K' → 1200, '53' → 53, '3.4M' → 3400000; None for missing or unparsable."""
    if not text:
        return None
    t = text.strip().replace(",", "").replace(" ", "")
    mult = _COUNT_SUFFIX.get(t[-1:].upper(), 1)
    num = t[:-1] if mult != 1 else t
    try:
        return int(float(num) * mult)
    except ValueError:
        return None


def html_to_text(node: Node | None) -> str:
    """Message body with ``<br>`` as newlines; the HTML itself is kept in ``raw``."""
    if node is None:
        return ""
    html = re.sub(r"<br\s*/?>", "\n", node.html or "", flags=re.IGNORECASE)
    return HTMLParser(html).text(separator="").strip()


def _decode_data_view(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        padded = value + "=" * (-len(value) % 4)
        return dict(json.loads(base64.b64decode(padded)))
    except (ValueError, TypeError):
        return {}


def _member_id(href: str | None, fallback: int) -> int:
    m = _SINGLE_RE.search(href or "")
    return int(m.group(1)) if m else fallback


def _media_items(node: Node) -> list[dict[str, Any]]:
    """Every downloadable/visible media item in a block, with the message id it belongs to."""
    items: list[dict[str, Any]] = []
    for a in node.css("a.tgme_widget_message_photo_wrap"):
        m = _BG_URL_RE.search(a.attributes.get("style") or "")
        items.append(
            {"type": "photo", "href": a.attributes.get("href"), "url": m.group(1) if m else None}
        )
    for a in node.css("a.tgme_widget_message_video_player"):
        thumb = a.css_first("i.tgme_widget_message_video_thumb")
        m = _BG_URL_RE.search((thumb.attributes.get("style") if thumb else None) or "")
        kind = (
            "animation" if a.css_first("i.tgme_widget_message_video_duration") is None else "video"
        )
        items.append(
            {"type": kind, "href": a.attributes.get("href"), "url": m.group(1) if m else None}
        )
    for s in node.css("div.tgme_widget_message_sticker_wrap"):
        icon = s.css_first("i.tgme_widget_message_sticker")
        m = _BG_URL_RE.search((icon.attributes.get("style") if icon else None) or "")
        url = m.group(1) if m else (icon.attributes.get("data-webp") if icon else None)
        items.append({"type": "sticker", "href": s.attributes.get("href"), "url": url})
    return items


def parse_message_block(node: Node, channel_username: str) -> list[RawMessage]:
    """One preview block → one RawMessage per Telegram message (albums expand to their members)."""
    post = node.attributes.get("data-post") or ""
    m = _POST_RE.match(post)
    if not m:
        return []
    block_id = int(m.group(2))
    time_node = node.css_first("time")
    date_raw = (time_node.attributes.get("datetime") if time_node else None) or ""
    try:
        date = datetime.fromisoformat(date_raw)
    except ValueError:
        date = datetime.now(UTC)
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)

    text_node = node.css_first("div.tgme_widget_message_text")
    text = html_to_text(text_node)
    views_node = node.css_first("span.tgme_widget_message_views")
    views = parse_count(views_node.text() if views_node else None)
    reactions: dict[str, int] = {}
    for r in node.css("span.tgme_reaction"):
        emoji_node = r.css_first("b")
        emoji = emoji_node.text(strip=True) if emoji_node else "?"
        count = parse_count(r.text(strip=True).replace(emoji, "", 1)) or 0
        reactions[emoji] = reactions.get(emoji, 0) + count
    fwd_node = node.css_first("a.tgme_widget_message_forwarded_from_name")
    fwd_username: str | None = None
    fwd_msg_id: int | None = None
    if fwd_node is not None:
        fm = _TME_RE.match(fwd_node.attributes.get("href") or "")
        if fm:
            fwd_username = fm.group(1)
            fwd_msg_id = int(fm.group(2)) if fm.group(2) else None

    media = _media_items(node)
    other_kind: str | None = None
    if not media:
        if node.css_first("a.tgme_widget_message_link_preview"):
            other_kind = "webpage"
        elif node.css_first("div.tgme_widget_message_document_wrap"):
            other_kind = "document"
        elif node.css_first("div.tgme_widget_message_poll"):
            other_kind = "poll"
        elif node.css_first("div.tgme_widget_message_roundvideo_wrap"):
            other_kind = "video"

    base_raw: dict[str, Any] = {
        "source": "web_preview",
        "post": post,
        "data_view": _decode_data_view(node.attributes.get("data-view")),
        "html_text": text_node.html if text_node else None,
        "views_raw": views_node.text() if views_node else None,
        "reactions": reactions,
        "forward_from_username": fwd_username,
    }
    common = {
        "date": date,
        "views": views,
        "forwards": None,  # not exposed by the preview
        "reactions_count": sum(reactions.values()) if reactions else None,
        "forward_from_channel": stable_id(fwd_username.lower()) if fwd_username else None,
        "forward_from_msg_id": fwd_msg_id,
    }

    if not media:
        return [
            RawMessage(
                id=block_id,
                text=text,
                media_type=other_kind,
                raw=base_raw | {"media": []},
                **common,  # type: ignore[arg-type]
            )
        ]

    grouped = len(media) > 1
    out: list[RawMessage] = []
    seen_ids: set[int] = set()
    for i, item in enumerate(media):
        mid = _member_id(item["href"], block_id if i == 0 else block_id + i)
        if mid in seen_ids:
            mid = block_id + i
        seen_ids.add(mid)
        url = item["url"]
        ext = ".webp" if item["type"] == "sticker" else ".jpg"
        out.append(
            RawMessage(
                id=mid,
                text=text if mid == block_id else "",
                media_type=item["type"],
                media_tg_id=stable_id(url) if url else None,
                media_ext=ext,
                grouped_id=block_id if grouped else None,
                raw=base_raw | {"media": [item]},
                native={"url": url},
                **common,  # type: ignore[arg-type]
            )
        )
    out.sort(key=lambda r: r.id)
    return out


def parse_page(html: str, username: str) -> tuple[ChannelInfo, list[RawMessage]]:
    """Channel header + every message on the page, ascending by id."""
    tree = HTMLParser(html)
    title_node = tree.css_first("div.tgme_channel_info_header_title")
    title = title_node.text(strip=True) if title_node else ""
    subscribers = None
    for counter in tree.css("div.tgme_channel_info_counter"):
        kind = counter.css_first("span.counter_type")
        value = counter.css_first("span.counter_value")
        if (
            kind is not None
            and value is not None
            and kind.text(strip=True).startswith("subscriber")
        ):
            subscribers = parse_count(value.text(strip=True))
    messages: list[RawMessage] = []
    channel_id: int | None = None
    for block in tree.css("div.tgme_widget_message"):
        if channel_id is None:
            c = _decode_data_view(block.attributes.get("data-view")).get("c")
            if isinstance(c, int):
                channel_id = abs(c)
        messages.extend(parse_message_block(block, username))
    messages.sort(key=lambda r: r.id)
    info = ChannelInfo(
        id=channel_id if channel_id is not None else stable_id(username.lower()),
        username=username,
        title=title,
        subscribers=subscribers,
    )
    return info, messages


class WebPreviewSource:
    """Polite sequential reader: one request at a time, a pause between them, resumable."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        delay_s: float = 0.8,
        media_delay_s: float = 0.15,
        proxy: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
            timeout=30.0,
            follow_redirects=True,
            proxy=proxy,
        )
        self._delay_s = delay_s
        self._media_delay_s = media_delay_s  # the CDN is built for volume; t.me pages are not
        self._sleep = sleep
        self._last_request = 0.0
        self.requests = 0

    async def __aenter__(self) -> WebPreviewSource:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(
        self, url: str, params: dict[str, Any] | None = None, *, delay_s: float | None = None
    ) -> httpx.Response:
        loop = asyncio.get_running_loop()
        wait = (self._delay_s if delay_s is None else delay_s) - (loop.time() - self._last_request)
        if wait > 0:
            await self._sleep(wait)
        attempts = 0
        while True:
            attempts += 1
            self._last_request = loop.time()
            self.requests += 1
            try:
                resp = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                if attempts >= 3:
                    raise FloodWait(120) from exc
                await self._sleep(5.0 * attempts)
                continue
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                raise FloodWait(int(retry_after) if retry_after and retry_after.isdigit() else 60)
            if resp.status_code >= 500 and attempts < 3:
                await self._sleep(5.0 * attempts)
                continue
            resp.raise_for_status()
            return resp

    async def fetch_page(
        self, username: str, before: int | None = None
    ) -> tuple[ChannelInfo, list[RawMessage]]:
        params = {"before": before} if before else None
        resp = await self._get(BASE_URL.format(username=username), params)
        return parse_page(resp.text, username)

    async def resolve_channel(self, username: str) -> ChannelInfo:
        info, messages = await self.fetch_page(username)
        if not messages:
            raise ValueError(f"@{username}: no public preview (private, restricted or missing)")
        return info

    async def iter_messages(
        self,
        channel: ChannelRef,
        *,
        min_id: int = 0,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        collected: list[RawMessage] = []
        before: int | None = None
        while True:
            _, page = await self.fetch_page(channel.username, before)
            if not page:
                break
            keep = [
                m
                for m in page
                if (not min_id or m.id > min_id) and (min_id or since is None or m.date >= since)
            ]
            collected.extend(keep)
            reached_boundary = len(keep) < len(page)
            oldest = min(m.id for m in page)
            if reached_boundary or oldest <= 1 or (before is not None and oldest >= before):
                break
            before = oldest
        collected.sort(key=lambda m: m.id)
        for m in collected[: limit or None]:
            yield m

    async def download_media(self, message: RawMessage, dest: Path) -> Path | None:
        url = (message.native or {}).get("url") if isinstance(message.native, dict) else None
        if not url:
            return None
        resp = await self._get(url, delay_s=self._media_delay_s)
        if not resp.content:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, resp.content)
        return dest
