"""Plain BM25 over a list of texts — the lexical baseline of the retrieval ablation (SPEC §8.1).

Kept deliberately simple: unicode word tokens, lower-cased, no stemming (Russian morphology is
therefore a known handicap for this baseline; BGE-M3's learned sparse weights are the
production lexical signal). Built in memory from the same texts the index holds, so the
comparison is apples to apples.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if not t.isdigit() or len(t) == 4]


@dataclass
class BM25Index:
    ids: list[int]
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._postings: dict[str, dict[int, int]] = {}
        self._lengths: dict[int, int] = {}
        self._avgdl = 0.0

    @classmethod
    def build(cls, ids: Sequence[int], texts: Sequence[str], **kw: float) -> BM25Index:
        idx = cls(list(ids), **kw)
        for pid, text in zip(ids, texts, strict=True):
            tokens = tokenize(text)
            idx._lengths[pid] = len(tokens)
            for tok, n in Counter(tokens).items():
                idx._postings.setdefault(tok, {})[pid] = n
        idx._avgdl = sum(idx._lengths.values()) / max(1, len(idx._lengths))
        return idx

    def idf(self, token: str) -> float:
        n = len(self._postings.get(token, ()))
        return math.log(1 + (len(self.ids) - n + 0.5) / (n + 0.5))

    def search(self, query: str, *, limit: int = 20) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for tok in set(tokenize(query)):
            postings = self._postings.get(tok)
            if not postings:
                continue
            idf = self.idf(tok)
            for pid, tf in postings.items():
                norm = self.k1 * (1 - self.b + self.b * self._lengths[pid] / max(self._avgdl, 1e-9))
                scores[pid] = scores.get(pid, 0.0) + idf * tf * (self.k1 + 1) / (tf + norm)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]
