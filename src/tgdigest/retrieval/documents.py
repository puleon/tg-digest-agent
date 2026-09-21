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
reachable whichever variant a query uses. Two hashes make re-indexing cheap: ``content_hash``
covers the texts (a change means re-embedding), ``meta_hash`` covers the payload fields that
come from labels and clusters (a change means rewriting the payload only).
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
    media_path: str | None = None
    """Relative path of the first member's media file (for display and the agent's get_post)."""
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
    meta_hash: str
    sources: dict[str, bool] = field(default_factory=dict)


def _joined(parts: list[str | None]) -> str:
    seen: list[str] = []
    for p in parts:
        t = normalize_text(p or "")
        if t and t not in seen:
            seen.append(t)
    return "\n".join(seen)[:MAX_SOURCE_CHARS]


def compose(sources: dict[str, str], variant: str) -> str:
    """The text of a variant from its sources — used at indexing time and for reranker passages."""
    order = {
        "text": ("text",),
        "text_ocr": ("text", "ocr"),
        "text_ocr_caption": ("text", "ocr", "caption"),
        "full": ("text", "ocr", "caption", "link"),
    }[variant]
    return "\n\n".join(sources.get(k) or "" for k in order if sources.get(k)).strip()


def passage_for(payload: dict[str, Any], variant: str = "full") -> str:
    """What a reranker or a judge reads for an indexed post."""
    sources = payload.get("sources") or {"text": payload.get("text", "")}
    return compose(dict(sources), variant)


def build_document(facts: PostFacts) -> IndexDocument:
    sources = {
        "text": normalize_text(facts.text)[:MAX_SOURCE_CHARS],
        "ocr": _joined([m.ocr_text for m in facts.members]),
        "caption": _joined([m.vlm_caption for m in facts.members]),
        "link": _joined([m.link_summary for m in facts.members]),
    }
    text, ocr, caption, link = (sources[k] for k in ("text", "ocr", "caption", "link"))
    variants = {v: compose(sources, v) for v in VARIANTS}
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
        "media_path": facts.media_path,
        "text": text[:SNIPPET_CHARS],
        "has_ocr": bool(ocr),
        "has_caption": bool(caption),
        "has_link": bool(link),
        "model_version": facts.model_version,
        "sources": {k: v for k, v in sources.items() if v},
    }

    def sha(parts: list[str]) -> str:
        return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()  # noqa: S324 - not security

    content_hash = sha([variants[v] for v in VARIANTS])
    meta_hash = sha(
        [
            str(payload[k])
            for k in (
                "channel",
                "topic",
                "label",
                "posted_at",
                "is_ad",
                "is_spoiler",
                "quality",
                "injection_flag",
                "cluster_id",
                "is_representative",
                "model_version",
                "media_path",
            )
        ]
    )
    payload["content_hash"] = content_hash
    payload["meta_hash"] = meta_hash
    return IndexDocument(
        post_id=facts.post_id,
        variants=variants,
        payload=payload,
        content_hash=content_hash,
        meta_hash=meta_hash,
        sources={"ocr": bool(ocr), "caption": bool(caption), "link": bool(link)},
    )
