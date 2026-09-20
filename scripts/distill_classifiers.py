"""D5 — distil the LLM classifiers into logistic regression over BGE-M3 embeddings.

uv run python scripts/distill_classifiers.py --max 3000 \
    --out docs/experiments/d5-distillation/results
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tgdigest.config import get_settings
from tgdigest.db.base import make_engine, make_session_factory
from tgdigest.db.models import Enrichment, Post
from tgdigest.ingest.distill import TARGETS, distill, encode_labels
from tgdigest.ingest.normalize import normalize_text
from tgdigest.retrieval.embeddings import BGEM3Embedder


async def load_rows(limit: int) -> list[dict[str, Any]]:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_session_factory(engine)() as session:
            stmt = (
                select(Post)
                .join(Enrichment, Enrichment.post_id == Post.id)
                .options(selectinload(Post.enrichment))
                .where(Enrichment.topic_labels.is_not(None))
                .order_by(Post.posted_at.desc())
                .limit(limit)
            )
            posts = list((await session.execute(stmt)).scalars().unique())
    finally:
        await engine.dispose()
    rows = []
    for p in posts:
        e = p.enrichment
        assert e is not None and e.topic_labels
        parts = [normalize_text(p.text)]
        if e.ocr_text:
            parts.append(e.ocr_text)
        if e.vlm_caption:
            parts.append(e.vlm_caption)
        if not any(parts):  # nothing the student could read: an album member without media
            continue
        rows.append(
            {
                "post_id": p.id,
                "text": "\n".join(parts)[:2000],
                "topic": e.topic_labels[0],
                "is_ad": e.is_ad,
                "is_spoiler": e.is_spoiler,
                "quality": int(e.quality_score or 3),
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3000)
    ap.add_argument("--out", type=Path, default=Path("docs/experiments/d5-distillation/results"))
    ap.add_argument("--models-dir", type=Path, default=Path("data/models"))
    args = ap.parse_args()

    rows = asyncio.run(load_rows(args.max))
    print(f"{len(rows)} teacher-labelled posts")
    embedder = BGEM3Embedder()
    t = time.perf_counter()
    batch = embedder.encode([r["text"] for r in rows], batch_size=16, sparse=False)
    embed_s = time.perf_counter() - t
    print(f"embedded in {embed_s:.0f}s ({len(rows) / embed_s:.1f} posts/s)")

    labels = encode_labels(rows)
    report = distill(batch.dense, labels)
    args.out.mkdir(parents=True, exist_ok=True)
    args.models_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "n_posts": len(rows),
        "n_train": report.n_train,
        "n_test": report.n_test,
        "embed_seconds": round(embed_s, 1),
        "topics": labels["_topics"].tolist(),
        "label_distribution": {
            "topic": {
                t: int(np.sum(labels["topic"] == i)) for i, t in enumerate(labels["_topics"])
            },
            "is_ad": int(labels["is_ad"].sum()),
            "is_spoiler": int(labels["is_spoiler"].sum()),
            "quality": {
                int(q): int(np.sum(labels["quality"] == q)) for q in np.unique(labels["quality"])
            },
        },
        "metrics": report.metrics,
    }
    (args.out / "distill.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    for target, clf in report.models.items():
        joblib.dump(
            {"model": clf, "topics": labels["_topics"].tolist()},
            args.models_dir / f"student_{target}.joblib",
        )
    print("| target | accuracy | macro-F1 | kappa | majority baseline |")
    print("|---|---|---|---|---|")
    for target in TARGETS:
        m = report.metrics.get(target)
        if m:
            print(
                f"| {target} | {m['accuracy']:.3f} | {m['macro_f1']:.3f} | {m['kappa']:.3f} "
                f"| {m['majority_baseline']:.3f} |"
            )


if __name__ == "__main__":
    main()
