"""Transport-independent view of a Telegram message, so sync logic never touches Telethon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

DOWNLOADABLE_TYPES = frozenset({"photo", "image"})
THUMBNAIL_TYPES = frozenset({"video", "animation"})


@dataclass(frozen=True)
class ChannelRef:
    """What a source needs to address a channel: MTProto wants the id, the web preview the name."""

    id: int
    username: str


@dataclass(frozen=True)
class ChannelInfo(ChannelRef):
    title: str = ""
    subscribers: int | None = None


@dataclass(frozen=True)
class RawMessage:
    id: int
    date: datetime
    text: str = ""
    media_type: str | None = None
    media_tg_id: int | None = None
    media_ext: str = ".jpg"
    views: int | None = None
    forwards: int | None = None
    reactions_count: int | None = None
    forward_from_channel: int | None = None
    forward_from_msg_id: int | None = None
    grouped_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    native: Any = field(default=None, compare=False, repr=False)
    """The underlying client object (a Telethon Message), needed only for downloads."""

    @property
    def wants_download(self) -> bool:
        return self.media_tg_id is not None and (
            self.media_type in DOWNLOADABLE_TYPES or self.media_type in THUMBNAIL_TYPES
        )


class TelegramSource(Protocol):
    async def resolve_channel(self, username: str) -> ChannelInfo: ...

    def iter_messages(
        self,
        channel: ChannelRef,
        *,
        min_id: int = 0,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        """Messages newer than ``min_id`` (or, on the first pass, not older than ``since``),
        in ascending id order — the sync commits its cursor per batch and relies on it."""
        ...

    async def download_media(self, message: RawMessage, dest: Path) -> Path | None:
        """Save the photo (or a video/animation thumbnail) to ``dest``; None if nothing saved."""
        ...


class FloodWait(Exception):
    """Telegram asked us to back off for ``seconds`` (translated from the client library)."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"flood wait {seconds}s")
        self.seconds = seconds
