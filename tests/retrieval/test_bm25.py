from __future__ import annotations

from tgdigest.retrieval.bm25 import BM25Index, tokenize


def test_tokenize_lowercases_and_keeps_years() -> None:
    assert tokenize("Дюна (2021) — Вильнёв, 12 серий") == ["дюна", "2021", "вильнёв", "серий"]


def test_rare_terms_outrank_common_ones() -> None:
    idx = BM25Index.build(
        [1, 2, 3, 4],
        [
            "мем про дедлайн и кота",
            "кот спит на клавиатуре",
            "дедлайн завтра, паника",
            "обзор фильма про кота-космонавта",
        ],
    )
    ranked = idx.search("дедлайн кота")
    assert [pid for pid, _ in ranked][:1] == [1]  # both terms
    assert {pid for pid, _ in ranked} == {1, 3, 4}  # "кот" ≠ "кота": no stemming, by design
    assert idx.search("несуществующее слово") == []
    assert idx.search("") == []


def test_length_normalisation_prefers_the_shorter_document() -> None:
    idx = BM25Index.build([1, 2], ["кот", "кот " + "слово " * 50])
    assert idx.search("кот")[0][0] == 1
