"""What gets indexed for a post, and in which variants (SPEC §6.4).

The indexable text of a post is ``text + ocr_text + vlm_caption + link_summary``. Each post is
stored under four *variants* so the retrieval ablation (§6.4, §8.1) can search exactly the
configuration it measures without rebuilding anything:

| variant              | content                                        |
|----------------------|------------------------------------------------|
| ``text``             | the post's own text                            |
| ``text_ocr``         | + OCR of its images                            |
| ``text_ocr_caption`` | + VLM captions of its images                   |
| ``full``             | + summaries of the links it carries            |

A post without images has identical variants; they are still all stored, so every post is
reachable whichever variant a query uses. ``content_hash`` covers the variants and the payload
fields that come from enrichment, so re-indexing is a no-op until something changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tgdigest.ingest.normalize import normalize_text

VARIANTS: tuple[str, ...] = ("text", "text_ocr", "text_ocr_caption", "full")
SNIPPET_CHARS = 400
MAX_SOURCE_CHARS = 4000  # BGE-M3 truncates at 1 024 tokens anyway; keep the payload sane


@dataclass(frozen=True)
class MemberEnrichment:
    """The per-message part of an enrichment row: images are described per album member."""

    ocr_text: str | None = None
    vlm_caption: str | None = None
    link_summary: str | None = None


@dataclass(frozen=True)
class PostFacts:
    """Everything the index needs about one post (album = one post, first member's id)."""

    post_id: int
    channel_id: int
    channel: str
    topic: str
    posted_at: datetime
    text: str
    media_type: str | None
    members: tuple[MemberEnrichment, ...] = ()
    label: str | None = None
    is_ad: bool | None = None
    is_spoiler: bool | None = None
    quality: float | None = None
    injection_flag: bool | None = None
    model_version: str | None = None
    cluster_id: int | None = None
    is_representative: bool = True
    """False only for a clustered post that is not its cluster's representative."""


@dataclass(frozen=True)
class IndexDocument:
    post_id: int
    variants: dict[str, str]
    payload: dict[str, Any]
    content_hash: str
    sources: dict[str, bool] = field(default_factory=dict)


def _joined(parts: list[str | None]) -> str:
    seen: list[str] = []
    for p in parts:
        t = normalize_text(p or "")
        if t and t not in seen:
            seen.append(t)
    return "\n".join(seen)[:MAX_SOURCE_CHARS]


def build_document(facts: PostFacts) -> IndexDocument:
    text = normalize_text(facts.text)[:MAX_SOURCE_CHARS]
    ocr = _joined([m.ocr_text for m in facts.members])
    caption = _joined([m.vlm_caption for m in facts.members])
    link = _joined([m.link_summary for m in facts.members])

    def stack(*parts: str) -> str:
        return "\n\n".join(p for p in parts if p).strip()

    variants = {
        "text": text,
        "text_ocr": stack(text, ocr),
        "text_ocr_caption": stack(text, ocr, caption),
        "full": stack(text, ocr, caption, link),
    }
    payload: dict[str, Any] = {
        "post_id": facts.post_id,
        "channel_id": facts.channel_id,
        "channel": facts.channel,
        "topic": facts.topic,
        "label": facts.label,
        "posted_at": int(facts.posted_at.timestamp()),
        "date": facts.posted_at.strftime("%Y-%m-%d"),
        "is_ad": facts.is_ad,
        "is_spoiler": facts.is_spoiler,
        "quality": facts.quality,
        "injection_flag": facts.injection_flag,
        "cluster_id": facts.cluster_id,
        "is_representative": facts.is_representative,
        "has_media": facts.media_type is not None,
        "media_type": facts.media_type,
        "text": text[:SNIPPET_CHARS],
        "has_ocr": bool(ocr),
        "has_caption": bool(caption),
        "has_link": bool(link),
        "model_version": facts.model_version,
    }
    digest = hashlib.sha1(  # noqa: S324 - change detection, not security
        "\x1f".join(
            [
                variants["full"],
                str(facts.label),
                str(facts.is_ad),
                str(facts.is_spoiler),
                str(facts.quality),
                str(facts.cluster_id),
                str(facts.is_representative),
                str(facts.model_version),
                str(facts.posted_at.timestamp()),
                facts.channel,
                facts.topic,
            ]
        ).encode()
    ).hexdigest()
    payload["content_hash"] = digest
    return IndexDocument(
        post_id=facts.post_id,
        variants=variants,
        payload=payload,
        content_hash=digest,
        sources={"ocr": bool(ocr), "caption": bool(caption), "link": bool(link)},
    )
