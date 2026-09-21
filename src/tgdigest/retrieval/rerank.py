"""Cross-encoder reranking of the top candidates (SPEC §6.4: "реранкер поверх топ-50").

``bge-reranker-v2-m3`` scores (query, passage) pairs jointly, which the bi-encoder cannot; it
costs a forward pass per pair, so it only sees the head of the candidate list.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Protocol

import structlog

from tgdigest.retrieval.documents import passage_for
from tgdigest.retrieval.index import Hit

log = structlog.get_logger(__name__)

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class BGEReranker:
    """FlagEmbedding cross-encoder on CPU, loaded lazily once per process."""

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        *,
        max_length: int = 1024,
        threads: int | None = None,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self._threads = threads
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            import torch
            from FlagEmbedding import FlagReranker

            if self._threads:
                torch.set_num_threads(self._threads)
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            log.info("loading_reranker", model=self.model_name, threads=torch.get_num_threads())
            self._model = FlagReranker(
                self.model_name,
                use_fp16=False,
                devices="cpu",
                batch_size=self.batch_size,
                max_length=self.max_length,
                normalize=True,
            )
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        out = self._load().compute_score([[query, p] for p in passages])
        scores = out if isinstance(out, list) else [out]
        return [float(s) for s in scores]


def rerank(
    query: str,
    hits: Sequence[Hit],
    reranker: Reranker,
    *,
    variant: str = "full",
    top_k: int | None = None,
    depth: int = 50,
) -> list[Hit]:
    """Re-order the first ``depth`` hits by cross-encoder score over the ``variant`` text of
    each post; the tail keeps its order."""
    head = list(hits[:depth])
    if not head:
        return list(hits)
    texts = [passage_for(h.payload, variant) for h in head]
    scores = reranker.score(query, texts)
    order = sorted(range(len(head)), key=lambda i: (-scores[i], head[i].rank))
    reranked = [replace(head[i], score=scores[i], rank=r + 1) for r, i in enumerate(order)]
    tail = [replace(h, rank=len(reranked) + j + 1) for j, h in enumerate(hits[depth:])]
    out = reranked + tail
    return out[:top_k] if top_k else out
