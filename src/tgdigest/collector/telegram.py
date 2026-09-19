"""Telethon adapter — the only module that imports Telethon."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import (
    Channel,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    PeerChannel,
)

from tgdigest.collector.types import ChannelInfo, ChannelRef, FloodWait, RawMessage

_MAX_IMAGE_DOCUMENT_BYTES = 10 * 1024 * 1024


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    return obj


def classify_media(message: Message) -> tuple[str | None, int | None, str]:
    """(media_type, telegram media id, file extension) for a message."""
    media = message.media
    if media is None:
        return None, None, ".jpg"
    if isinstance(media, MessageMediaPhoto) and media.photo is not None:
        return "photo", getattr(media.photo, "id", None), ".jpg"
    if isinstance(media, MessageMediaWebPage):
        return "webpage", None, ".jpg"
    if isinstance(media, MessageMediaDocument) and media.document is not None:
        doc = media.document
        attrs = getattr(doc, "attributes", []) or []
        mime = getattr(doc, "mime_type", "") or ""
        if any(isinstance(a, DocumentAttributeSticker) for a in attrs):
            return "sticker", doc.id, ".webp"
        if any(isinstance(a, DocumentAttributeAnimated) for a in attrs):
            return "animation", doc.id, ".jpg"
        if any(isinstance(a, DocumentAttributeVideo) for a in attrs):
            return "video", doc.id, ".jpg"
        if any(isinstance(a, DocumentAttributeAudio) for a in attrs):
            kind = "voice" if any(getattr(a, "voice", False) for a in attrs) else "audio"
            return kind, doc.id, ".ogg"
        if mime.startswith("image/") and (doc.size or 0) <= _MAX_IMAGE_DOCUMENT_BYTES:
            ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(
                mime, ".jpg"
            )
            return "image", doc.id, ext
        return "document", doc.id, ""
    return "other", None, ""


def to_raw(message: Message) -> RawMessage:
    media_type, media_tg_id, ext = classify_media(message)
    fwd = message.fwd_from
    fwd_channel = None
    if fwd is not None and isinstance(fwd.from_id, PeerChannel):
        fwd_channel = fwd.from_id.channel_id
    reactions = None
    if message.reactions is not None and message.reactions.results:
        reactions = sum(r.count for r in message.reactions.results)
    date = message.date or datetime.now(UTC)
    return RawMessage(
        id=message.id,
        date=date if date.tzinfo else date.replace(tzinfo=UTC),
        text=message.message or "",
        media_type=media_type,
        media_tg_id=media_tg_id,
        media_ext=ext,
        views=message.views,
        forwards=message.forwards,
        reactions_count=reactions,
        forward_from_channel=fwd_channel,
        forward_from_msg_id=getattr(fwd, "channel_post", None) if fwd else None,
        grouped_id=message.grouped_id,
        raw=_json_safe(json.loads(message.to_json())),
        native=message,
    )


class TelethonSource:
    """Reads public channels with a *user* session (SPEC §4: Bot API cannot read channels)."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, api_id: int, api_hash: str, session_path: str) -> TelethonSource:
        client = TelegramClient(
            session_path,
            api_id,
            api_hash,
            flood_sleep_threshold=120,  # Telethon sleeps itself for short waits (SPEC §6.1)
        )
        return cls(client)

    async def __aenter__(self) -> TelethonSource:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized — run `tgdigest collector login` once "
                "in an interactive terminal on this machine."
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.disconnect()

    async def login_interactive(self) -> None:
        await self._client.start()  # prompts for phone / code / 2FA
        me = await self._client.get_me()
        print(f"authorized as {getattr(me, 'username', None) or getattr(me, 'id', '?')}")
        await self._client.disconnect()

    async def resolve_channel(self, username: str) -> ChannelInfo:
        try:
            return await self._resolve_channel(username)
        except FloodWaitError as exc:
            raise FloodWait(exc.seconds) from exc

    async def _resolve_channel(self, username: str) -> ChannelInfo:
        entity = await self._client.get_entity(username)
        if not isinstance(entity, Channel):
            raise ValueError(f"@{username} is not a channel")
        full = await self._client(GetFullChannelRequest(entity))
        return ChannelInfo(
            id=entity.id,
            username=username,
            title=entity.title or "",
            subscribers=getattr(full.full_chat, "participants_count", None),
        )

    async def iter_messages(
        self,
        channel: ChannelRef,
        *,
        min_id: int = 0,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        entity = await self._client.get_input_entity(PeerChannel(channel.id))
        kwargs: dict[str, Any] = {"reverse": True, "limit": limit}
        if min_id:
            kwargs["min_id"] = min_id
        elif since is not None:
            kwargs["offset_date"] = since
        try:
            async for message in self._client.iter_messages(entity, **kwargs):
                if isinstance(message, Message):
                    yield to_raw(message)
        except FloodWaitError as exc:
            raise FloodWait(exc.seconds) from exc

    async def download_media(self, message: RawMessage, dest: Path) -> Path | None:
        native = message.native
        if native is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        thumb = -1 if message.media_type in {"video", "animation"} else None
        result = await self._client.download_media(native, file=str(dest), thumb=thumb)
        return Path(result) if result else None
