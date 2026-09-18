"""Media files on disk, deduplicated by Telegram media id (SPEC §6.1)."""

from __future__ import annotations

from pathlib import Path

from tgdigest.collector.types import RawMessage, TelegramSource


class MediaStore:
    """``<root>/<shard>/<media_tg_id><ext>`` — identical media shares one file across channels."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def relative_path(self, message: RawMessage) -> Path:
        assert message.media_tg_id is not None
        return (
            Path(f"{message.media_tg_id % 1000:03d}") / f"{message.media_tg_id}{message.media_ext}"
        )

    async def fetch(self, source: TelegramSource, message: RawMessage) -> tuple[str | None, bool]:
        """Return (path relative to root, reused). Download only when the file is missing."""
        if not message.wants_download:
            return None, False
        rel = self.relative_path(message)
        dest = self.root / rel
        if dest.exists() and dest.stat().st_size > 0:
            return str(rel), True
        saved = await source.download_media(message, dest)
        if saved is None:
            return None, False
        if saved != dest:  # the client may append its own extension
            saved.replace(dest)
        return str(rel), False
