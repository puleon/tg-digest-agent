"""Ranking metrics for the retrieval evaluation (SPEC §8.1): recall@k, nDCG@k, MRR.

Relevance is graded: 0 = not relevant, 1 = partially, 2 = relevant. Recall and MRR count a
document as relevant when its grade is ≥ 1; nDCG uses the grades. Documents outside the judged
pool count as not relevant — the usual pooling assumption, stated in the report.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryMetrics:
    recall_at_20: float
    ndcg_at_10: float
    mrr: float
    relevant_total: int
    judged_in_top_20: int


def recall_at_k(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float:
    relevant = {d for d, g in grades.items() if g >= 1}
    if not relevant:
        return float("nan")
    return len(relevant & set(ranked[:k])) / len(relevant)


def dcg_at_k(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float:
    gains = [(2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k])]
    return float(sum(gains))


def ndcg_at_k(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float:
    ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
    if not ideal:
        return float("nan")
    idcg = float(sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal)))
    return dcg_at_k(ranked, grades, k) / idcg


def reciprocal_rank(ranked: Sequence[int], grades: Mapping[int, int]) -> float:
    if not any(g >= 1 for g in grades.values()):
        return float("nan")
    for i, d in enumerate(ranked):
        if grades.get(d, 0) >= 1:
            return 1.0 / (i + 1)
    return 0.0


def evaluate(ranked: Sequence[int], grades: Mapping[int, int]) -> QueryMetrics:
    return QueryMetrics(
        recall_at_20=recall_at_k(ranked, grades, 20),
        ndcg_at_10=ndcg_at_k(ranked, grades, 10),
        mrr=reciprocal_rank(ranked, grades),
        relevant_total=sum(1 for g in grades.values() if g >= 1),
        judged_in_top_20=sum(1 for d in ranked[:20] if d in grades),
    )


def mean(values: Sequence[float]) -> float:
    xs = [v for v in values if not math.isnan(v)]
    return sum(xs) / len(xs) if xs else float("nan")
