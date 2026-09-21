from __future__ import annotations

from datetime import UTC, datetime

from tgdigest.retrieval.documents import MemberEnrichment, PostFacts, build_document

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _facts(**kw: object) -> PostFacts:
    base: dict[str, object] = {
        "post_id": 1,
        "channel_id": 10,
        "channel": "memes",
        "topic": "humor",
        "posted_at": T0,
        "text": "Когда дедлайн  завтра",
        "media_type": "photo",
    }
    base.update(kw)
    return PostFacts(**base)  # type: ignore[arg-type]


def test_variants_stack_sources_and_drop_duplicates() -> None:
    doc = build_document(
        _facts(
            members=(
                MemberEnrichment(ocr_text="ВСЁ ГОРИТ", vlm_caption="a cat at a laptop"),
                MemberEnrichment(ocr_text="ВСЁ ГОРИТ", vlm_caption="the same cat, closer"),
                MemberEnrichment(link_summary="an article about burnout"),
            )
        )
    )
    assert doc.variants["text"] == "Когда дедлайн завтра"
    assert doc.variants["text_ocr"] == "Когда дедлайн завтра\n\nВСЁ ГОРИТ"  # OCR repeated once
    assert doc.variants["text_ocr_caption"].endswith("a cat at a laptop\nthe same cat, closer")
    assert doc.variants["full"].endswith("an article about burnout")
    assert doc.sources == {"ocr": True, "caption": True, "link": True}
    assert doc.payload["has_ocr"] and doc.payload["has_caption"] and doc.payload["has_link"]


def test_post_without_images_has_identical_variants() -> None:
    doc = build_document(_facts(media_type=None))
    assert len({doc.variants[v] for v in doc.variants}) == 1
    assert doc.sources == {"ocr": False, "caption": False, "link": False}
    assert doc.payload["has_media"] is False


def test_hash_tracks_content_and_labels_only() -> None:
    a = build_document(_facts())
    same = build_document(_facts())
    relabelled = build_document(_facts(label="cinema"))
    clustered = build_document(_facts(cluster_id=7, is_representative=False))
    new_ocr = build_document(_facts(members=(MemberEnrichment(ocr_text="text on the image"),)))
    assert a.content_hash == same.content_hash
    assert (
        len({a.content_hash, relabelled.content_hash, clustered.content_hash, new_ocr.content_hash})
        == 4
    )


def test_payload_carries_filter_fields() -> None:
    doc = build_document(_facts(is_ad=True, is_spoiler=False, quality=0.4, cluster_id=3))
    p = doc.payload
    assert p["posted_at"] == int(T0.timestamp()) and p["date"] == "2026-06-01"
    assert p["is_ad"] is True and p["is_spoiler"] is False and p["quality"] == 0.4
    assert p["cluster_id"] == 3 and p["is_representative"] is True
    assert p["text"] == "Когда дедлайн завтра" and p["content_hash"] == doc.content_hash
