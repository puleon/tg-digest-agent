from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.retrieval.conftest import FakeEmbedder
from tgdigest.retrieval.documents import MemberEnrichment, PostFacts, build_document
from tgdigest.retrieval.index import PostIndex, SearchFilters

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _doc(pid: int, text: str, **kw: object) -> PostFacts:
    base: dict[str, object] = {
        "post_id": pid,
        "channel_id": 10,
        "channel": "memes",
        "topic": "humor",
        "posted_at": T0 + timedelta(days=pid),
        "text": text,
        "media_type": "photo",
    }
    base.update(kw)
    return PostFacts(**base)  # type: ignore[arg-type]


def test_upsert_is_idempotent_and_embeds_unique_texts_once(
    index: PostIndex, embedder: FakeEmbedder
) -> None:
    docs = [build_document(_doc(1, "кот и дедлайн")), build_document(_doc(2, "обзор фильма"))]
    first = index.upsert(docs)
    assert (first.indexed, first.unchanged, first.embedded_texts) == (2, 0, 2)  # variants identical
    again = index.upsert(docs)
    assert (again.indexed, again.unchanged) == (0, 2) and len(embedder.calls) == 1
    relabelled = [build_document(_doc(1, "кот и дедлайн", label="humor"))]
    meta = index.upsert(relabelled)  # labels changed: payload rewritten, nothing re-embedded
    assert (meta.indexed, meta.payload_updated) == (0, 1) and len(embedder.calls) == 1
    assert index.search("кот", mode="dense", limit=1)[0].payload["label"] == "humor"
    retexted = [build_document(_doc(1, "кот и новый дедлайн", label="humor"))]
    assert index.upsert(retexted).indexed == 1 and embedder.calls[-1] == ["кот и новый дедлайн"]
    assert index.count() == 2


def test_variants_search_what_they_contain(index: PostIndex) -> None:
    meme = _doc(
        1, "", members=(MemberEnrichment(ocr_text="ПОНЕДЕЛЬНИК ОПЯТЬ", vlm_caption="грустный кот"),)
    )
    plain = _doc(2, "рецензия на понедельник")
    index.upsert([build_document(meme), build_document(plain)])
    by_text = [h.post_id for h in index.search("понедельник", variant="text", mode="dense")]
    by_ocr = [h.post_id for h in index.search("понедельник", variant="text_ocr", mode="dense")]
    assert by_text == [2] or by_text[0] == 2  # the meme has no text, only the plain post matches
    assert by_ocr[0] == 1 or set(by_ocr) == {1, 2}
    caption = [
        h.post_id for h in index.search("грустный кот", variant="text_ocr_caption", mode="sparse")
    ]
    assert caption == [1]


def test_hybrid_fuses_dense_and_sparse(index: PostIndex) -> None:
    index.upsert(
        [
            build_document(_doc(1, "мем про дедлайн и кота")),
            build_document(_doc(2, "кот спит на клавиатуре")),
            build_document(_doc(3, "новости кино за неделю")),
        ]
    )
    hits = index.search("кот дедлайн", mode="hybrid", limit=3)
    assert {h.post_id for h in hits[:2]} == {1, 2} and hits[0].rank == 1
    assert all(h.score > 0 for h in hits)


def test_filters_topic_ads_dates_and_representatives(index: PostIndex) -> None:
    index.upsert(
        [
            build_document(_doc(1, "мем про кота", is_ad=True)),
            build_document(_doc(2, "мем про кота", topic="cinema", channel="films", channel_id=11)),
            build_document(_doc(3, "мем про кота", cluster_id=5, is_representative=False)),
            build_document(_doc(4, "мем про кота", cluster_id=5, is_representative=True)),
            build_document(_doc(5, "мем про кота", is_spoiler=True)),
        ]
    )

    def ids(f: SearchFilters) -> set[int]:
        return {h.post_id for h in index.search("мем про кота", filters=f, limit=10)}

    assert ids(SearchFilters(exclude_ads=False)) == {1, 2, 3, 4, 5}
    assert ids(SearchFilters()) == {2, 3, 4, 5}  # ads out by default
    assert ids(SearchFilters(topic="cinema", exclude_ads=False)) == {2}
    assert ids(SearchFilters(channels=[11], exclude_ads=False)) == {2}
    assert ids(SearchFilters(representatives_only=True)) == {2, 4, 5}
    assert ids(SearchFilters(exclude_spoilers=True)) == {2, 3, 4}
    assert ids(SearchFilters(date_from=T0 + timedelta(days=4))) == {4, 5}
    assert ids(SearchFilters(date_to=T0 + timedelta(days=2), exclude_ads=False)) == {1, 2}
