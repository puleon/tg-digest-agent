"""Search pipelines built from the index primitives: multi-query fusion, reranking, rewriting.

Reciprocal-rank fusion is used twice: inside Qdrant for dense + sparse of one query, and here
to merge the result lists of several rewritten queries — a post found by two phrasings ranks
above one found by a single phrasing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tgdigest.retrieval.index import Hit, Mode, PostIndex, SearchFilters
from tgdigest.retrieval.rerank import Reranker, rerank

RRF_K = 60


def rrf_fuse(
    lists: Sequence[Sequence[Hit]], *, k: int = RRF_K, limit: int | None = None
) -> list[Hit]:
    """Merge ranked lists by reciprocal rank; ties keep the earlier list's order."""
    score: dict[int, float] = {}
    first: dict[int, Hit] = {}
    for hits in lists:
        for h in hits:
            score[h.post_id] = score.get(h.post_id, 0.0) + 1.0 / (k + h.rank)
            first.setdefault(h.post_id, h)
    order = sorted(score, key=lambda pid: (-score[pid], first[pid].rank))
    fused = [replace(first[pid], score=score[pid], rank=r + 1) for r, pid in enumerate(order)]
    return fused[:limit] if limit else fused


@dataclass
class SearchConfig:
    variant: str = "full"
    mode: Mode = "hybrid"
    limit: int = 20
    candidates: int = 100
    rerank_depth: int = 0
    """> 0: rerank this many head candidates with the cross-encoder."""


def run_search(
    index: PostIndex,
    queries: Sequence[str],
    *,
    config: SearchConfig,
    filters: SearchFilters | None = None,
    reranker: Reranker | None = None,
) -> list[Hit]:
    """One or several query phrasings → fused candidates → optional rerank → top ``limit``."""
    depth = max(config.limit, config.rerank_depth)
    lists = [
        index.search(
            q,
            variant=config.variant,
            mode=config.mode,
            filters=filters,
            limit=depth,
            candidates=config.candidates,
        )
        for q in queries
    ]
    hits = rrf_fuse(lists) if len(lists) > 1 else list(lists[0])
    if config.rerank_depth and reranker is not None:
        # the rewritten phrasings are for recall; the cross-encoder judges against the original
        hits = rerank(queries[0], hits, reranker, variant=config.variant, depth=config.rerank_depth)
    return hits[: config.limit]
