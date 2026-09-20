"""Distil the few-shot LLM classifiers into light models (SPEC §6.2 step 3, D5).

Teacher = labels the fast LLM wrote into ``enrichment``; student = logistic regression on
BGE-M3 dense embeddings of the same input text. The number that matters is student/teacher
agreement on a held-out split (accuracy, macro-F1, Cohen's kappa), per label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, cohen_kappa_score, f1_score
from sklearn.model_selection import train_test_split

TARGETS = ("topic", "is_ad", "is_spoiler", "quality")


@dataclass
class DistillReport:
    n_train: int
    n_test: int
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    """target → {accuracy, macro_f1, kappa, majority_baseline}."""
    models: dict[str, Any] = field(default_factory=dict)


def _fit_predict(x_tr: np.ndarray, y_tr: np.ndarray, x_te: np.ndarray) -> tuple[Any, np.ndarray]:
    clf = LogisticRegression(max_iter=2000, C=2.0, class_weight="balanced")
    clf.fit(x_tr, y_tr)
    return clf, clf.predict(x_te)


def distill(
    x: np.ndarray, labels: dict[str, np.ndarray], *, test_size: float = 0.3, seed: int = 42
) -> DistillReport:
    """``labels[target]`` aligned with rows of ``x``; targets with a single class are skipped."""
    idx = np.arange(len(x))
    strat = labels["topic"] if len(np.unique(labels["topic"])) > 1 else None
    tr, te = train_test_split(idx, test_size=test_size, random_state=seed, stratify=strat)
    report = DistillReport(n_train=len(tr), n_test=len(te))
    for target in TARGETS:
        y = labels[target]
        if len(np.unique(y[tr])) < 2:
            continue
        clf, pred = _fit_predict(x[tr], y[tr], x[te])
        majority = np.bincount(y[tr].astype(int)).argmax() if y.dtype.kind in "iub" else None
        report.metrics[target] = {
            "accuracy": float(accuracy_score(y[te], pred)),
            "macro_f1": float(f1_score(y[te], pred, average="macro")),
            "kappa": float(cohen_kappa_score(y[te], pred)),
            "majority_baseline": float(np.mean(y[te] == majority))
            if majority is not None
            else float("nan"),
            "positives_test": float(np.mean(y[te])) if y.dtype.kind == "b" else float("nan"),
        }
        report.models[target] = clf
    return report


def encode_labels(rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    topics = sorted({r["topic"] for r in rows})
    topic_idx = {t: i for i, t in enumerate(topics)}
    return {
        "topic": np.array([topic_idx[r["topic"]] for r in rows], dtype=np.int64),
        "is_ad": np.array([bool(r["is_ad"]) for r in rows], dtype=bool),
        "is_spoiler": np.array([bool(r["is_spoiler"]) for r in rows], dtype=bool),
        "quality": np.array([int(r["quality"]) for r in rows], dtype=np.int64),
        "_topics": np.array(topics),
    }
