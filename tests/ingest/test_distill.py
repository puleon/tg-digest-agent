from __future__ import annotations

import numpy as np

from tgdigest.ingest.distill import distill, encode_labels


def test_distill_reports_agreement_on_separable_synthetic_data() -> None:
    rng = np.random.default_rng(0)
    n = 300
    x = rng.normal(size=(n, 8)).astype(np.float32)
    rows = []
    for i in range(n):
        topic = "cinema" if x[i, 0] < -0.5 else ("humor" if x[i, 0] < 0.5 else "scifi")
        rows.append(
            {
                "topic": topic,
                "is_ad": bool(x[i, 2] > 0.8),
                "is_spoiler": False,  # single class: must be skipped, not crash
                "quality": int(np.clip(3 + x[i, 3], 1, 5)),
            }
        )
    labels = encode_labels(rows)
    assert labels["_topics"].tolist() == ["cinema", "humor", "scifi"]
    report = distill(x, labels, test_size=0.3, seed=1)
    assert report.n_train + report.n_test == n
    assert set(report.metrics) == {"topic", "is_ad", "quality"}
    assert report.metrics["topic"]["accuracy"] > 0.8 and report.metrics["topic"]["kappa"] > 0.7
    assert report.metrics["is_ad"]["accuracy"] > 0.85
    assert 0 <= report.metrics["quality"]["majority_baseline"] <= 1


def test_distill_skips_a_target_whose_test_split_has_one_class() -> None:
    rng = np.random.default_rng(3)
    n = 200
    x = rng.normal(size=(n, 4)).astype(np.float32)
    rows = [
        {
            "topic": "cinema" if x[i, 0] < 0 else "humor",
            "is_ad": i == 0,  # one positive: it lands in one split, never both
            "is_spoiler": False,
            "quality": 3,
        }
        for i in range(n)
    ]
    report = distill(x, encode_labels(rows), test_size=0.3, seed=1)
    assert "is_ad" in report.skipped and "is_ad" not in report.metrics
    assert "is_spoiler" in report.skipped and "quality" in report.skipped
    topic = report.details["topic"]
    assert topic["classes"] == ["cinema", "humor"]
    assert sum(map(sum, topic["confusion"])) == report.n_test
    assert set(topic["per_class"]["cinema"]) == {"precision", "recall", "f1", "support"}
