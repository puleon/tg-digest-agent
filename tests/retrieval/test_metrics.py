from __future__ import annotations

import math

from tgdigest.retrieval.metrics import (
    evaluate,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_mrr_and_ndcg_on_a_small_ranking() -> None:
    grades = {1: 2, 2: 1, 3: 0, 4: 2}  # relevant: 1, 2, 4
    ranked = [3, 1, 5, 2]
    assert recall_at_k(ranked, grades, 20) == 2 / 3
    assert reciprocal_rank(ranked, grades) == 0.5
    # DCG: 3@rank1 → 0 ; 1@rank2 → (2^2-1)/log2(3) ; 5 → 0 ; 2@rank4 → 1/log2(5)
    dcg = 3 / math.log2(3) + 1 / math.log2(5)
    idcg = 3 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    assert math.isclose(ndcg_at_k(ranked, grades, 10), dcg / idcg)
    assert ndcg_at_k([1, 4, 2], grades, 10) == 1.0


def test_queries_without_relevant_documents_are_left_out_of_the_mean() -> None:
    assert math.isnan(recall_at_k([1, 2], {1: 0}, 20))
    assert math.isnan(reciprocal_rank([1], {}))
    assert reciprocal_rank([9, 8], {1: 2}) == 0.0
    assert mean([0.5, float("nan"), 1.0]) == 0.75 and math.isnan(mean([]))


def test_evaluate_counts_judged_documents() -> None:
    m = evaluate([1, 2, 3], {1: 1, 3: 0, 4: 2})
    assert (m.relevant_total, m.judged_in_top_20) == (2, 2)
    assert m.recall_at_20 == 0.5 and m.mrr == 1.0
