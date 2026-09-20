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
