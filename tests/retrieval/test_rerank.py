from __future__ import annotations

from collections.abc import Sequence

from tgdigest.retrieval.index import Hit
from tgdigest.retrieval.rerank import rerank


class KeywordReranker:
    """Scores a passage by how many query words it contains (a cross-encoder stand-in)."""

    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        self.seen.append(list(passages))
        words = set(query.lower().split())
        return [sum(w in p.lower() for w in words) / max(1, len(words)) for p in passages]


def _hit(pid: int, rank: int, text: str, ocr: str = "") -> Hit:
    sources = {"text": text} | ({"ocr": ocr} if ocr else {})
    return Hit(post_id=pid, score=1.0 / rank, rank=rank, payload={"text": text, "sources": sources})


def test_rerank_reorders_the_head_and_keeps_the_tail() -> None:
    hits = [
        _hit(1, 1, "новости кино"),
        _hit(2, 2, "кот и дедлайн"),
        _hit(3, 3, "дедлайн: кот паникует"),
        _hit(4, 4, "погода"),
    ]
    out = rerank("кот дедлайн", hits, KeywordReranker(), depth=3)
    assert [h.post_id for h in out] == [2, 3, 1, 4]  # tie at 1.0 broken by the original rank
    assert [h.rank for h in out] == [1, 2, 3, 4] and out[0].score == 1.0
    assert [h.post_id for h in rerank("q", hits, KeywordReranker(), depth=3, top_k=2)] == [1, 2]
    assert rerank("q", [], KeywordReranker()) == []


def test_rerank_reads_the_requested_variant() -> None:
    hits = [_hit(1, 1, "подпись без слов"), _hit(2, 2, "", ocr="ПОНЕДЕЛЬНИК ОПЯТЬ")]
    rr = KeywordReranker()
    assert rerank("понедельник", hits, rr, variant="text_ocr")[0].post_id == 2
    assert rr.seen[-1][1] == "ПОНЕДЕЛЬНИК ОПЯТЬ"
    assert rerank("понедельник", hits, rr, variant="text")[0].post_id == 1  # OCR hidden: tie → rank
