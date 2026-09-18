"""Telethon → RawMessage mapping, built from real TL objects (no network)."""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    InputStickerSetEmpty,
    Message,
    MessageFwdHeader,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageReactions,
    PeerChannel,
    Photo,
    ReactionCount,
    ReactionEmoji,
)

from tgdigest.collector.telegram import classify_media, to_raw


def _photo(pid: int = 11) -> MessageMediaPhoto:
    return MessageMediaPhoto(
        photo=Photo(
            id=pid, access_hash=1, file_reference=b"", date=datetime.now(UTC), sizes=[], dc_id=2
        )
    )


def _doc(mime: str, *attrs: object, size: int = 1000) -> MessageMediaDocument:
    return MessageMediaDocument(
        document=Document(
            id=22,
            access_hash=1,
            file_reference=b"",
            date=datetime.now(UTC),
            mime_type=mime,
            size=size,
            dc_id=2,
            attributes=list(attrs),
        )
    )


def _message(**kwargs: object) -> Message:
    base: dict[str, object] = {
        "id": 5,
        "peer_id": PeerChannel(1001),
        "date": datetime(2026, 3, 1, tzinfo=UTC),
    }
    return Message(**(base | kwargs))


def test_classify_media() -> None:
    assert classify_media(_message()) == (None, None, ".jpg")
    assert classify_media(_message(media=_photo(11))) == ("photo", 11, ".jpg")
    video = _doc("video/mp4", DocumentAttributeVideo(duration=3, w=1, h=1))
    assert classify_media(_message(media=video))[:2] == ("video", 22)
    png = _doc("image/png", DocumentAttributeFilename("meme.png"))
    assert classify_media(_message(media=png)) == ("image", 22, ".png")
    huge = _doc("image/png", size=50_000_000)
    assert classify_media(_message(media=huge))[0] == "document"
    sticker = _doc(
        "image/webp", DocumentAttributeSticker(alt="", stickerset=InputStickerSetEmpty())
    )
    assert classify_media(_message(media=sticker))[0] == "sticker"


def test_to_raw_maps_forward_reactions_and_album() -> None:
    m = _message(
        message="ha",
        media=_photo(11),
        grouped_id=99,
        views=10,
        forwards=2,
        fwd_from=MessageFwdHeader(
            date=datetime.now(UTC), from_id=PeerChannel(2002), channel_post=17
        ),
        reactions=MessageReactions(
            results=[
                ReactionCount(reaction=ReactionEmoji("👍"), count=3),
                ReactionCount(reaction=ReactionEmoji("🔥"), count=4),
            ]
        ),
    )
    raw = to_raw(m)
    assert (raw.id, raw.text, raw.media_type, raw.media_tg_id) == (5, "ha", "photo", 11)
    assert (raw.forward_from_channel, raw.forward_from_msg_id, raw.grouped_id) == (2002, 17, 99)
    assert raw.reactions_count == 7 and raw.views == 10
    assert raw.date.tzinfo is not None
    assert raw.raw["id"] == 5 and raw.wants_download and raw.native is m
