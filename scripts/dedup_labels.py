"""D6 — hand-labelled pairs for dedup precision/recall (SPEC §6.3 metric).

sample  draw pairs: members of found clusters (precision) + near misses just outside the
        thresholds — pHash distance 7..12, TF-IDF cosine >= 0.5 — (recall); write labels.csv,
        copy images, build sheet.html for the human
score   pairwise precision / recall of the current clusters against labels.csv

uv run python scripts/dedup_labels.py sample --out data/eval/dedup
uv run python scripts/dedup_labels.py score --dir data/eval/dedup
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import random
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tgdigest.config import get_settings
from tgdigest.db.base import make_engine, make_session_factory
from tgdigest.db.models import Channel, Cluster, Post, PostCluster, PostSignature
from tgdigest.dedup.cluster import _popcount
from tgdigest.dedup.runner import _to_unsigned

STYLE = """
body{font-family:system-ui,sans-serif;max-width:1200px;margin:20px auto;padding:0 12px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px;border-top:2px solid #ccc;padding:14px 0}
.post{border:1px solid #ddd;padding:8px}img{max-width:100%;max-height:360px}
pre{white-space:pre-wrap;font-size:13px;background:#f5f5f5;padding:6px}
h3{margin:0 0 6px}small{color:#666}
"""


async def _load(session: AsyncSession) -> dict[str, Any]:
    posts = {
        p.id: (p, topic)
        for p, topic in (
            await session.execute(
                select(Post, Channel.topic).join(Channel, Channel.id == Post.channel_id)
            )
        ).all()
    }
    sigs = {s.post_id: s for s in (await session.execute(select(PostSignature))).scalars()}
    members: dict[int, list[int]] = defaultdict(list)
    stages: dict[int, str] = {}
    for pc in (await session.execute(select(PostCluster))).scalars():
        members[pc.cluster_id].append(pc.post_id)
        stages[pc.post_id] = pc.stage or ""
    clusters = {c.id: c for c in (await session.execute(select(Cluster))).scalars()}
    return {
        "posts": posts,
        "sigs": sigs,
        "members": members,
        "stages": stages,
        "clusters": clusters,
    }


def _post_key(post: Post) -> tuple[int, int]:
    return (post.channel_id, post.grouped_id if post.grouped_id is not None else -post.id)


def _near_miss_phash(
    data: dict[str, Any], lo: int, hi: int, n: int, rng: random.Random
) -> list[tuple[int, int, str]]:
    ids, hashes = [], []
    for pid, s in data["sigs"].items():
        h = _to_unsigned(s.phash)
        if h is not None:
            ids.append(pid)
            hashes.append(h)
    arr = np.array(hashes, dtype=np.uint64)
    idx = np.array(ids)
    same_cluster = _cluster_of(data)
    out: set[tuple[int, int]] = set()
    for start in range(0, len(arr), 2048):
        dist = _popcount(arr[start : start + 2048, None] ^ arr[None, :])
        ii, jj = np.nonzero((dist >= lo) & (dist <= hi))
        for i, j in zip(ii, jj, strict=True):
            a, b = int(idx[start + i]), int(idx[j])
            if (a < b and same_cluster.get(a) != same_cluster.get(b)) or (
                a < b and same_cluster.get(a) is None
            ):
                out.add((a, b))
    pairs = sorted(out)
    rng.shuffle(pairs)
    return [(a, b, "near_phash") for a, b in pairs[:n]]


def _cluster_of(data: dict[str, Any]) -> dict[int, int]:
    return {pid: cid for cid, mem in data["members"].items() for pid in mem}


def _near_miss_text(data: dict[str, Any], n: int, rng: random.Random) -> list[tuple[int, int, str]]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    ids = [
        pid
        for pid, s in data["sigs"].items()
        if (s.text_key and data["posts"][pid][0].grouped_id is None)
        or (s.text_key and data["posts"][pid][0].text)
    ]
    ids = [pid for pid in ids if len(data["sigs"][pid].text_key or "") >= 40]
    texts = [data["sigs"][pid].text_key for pid in ids]
    if len(texts) < 2:
        return []
    x = TfidfVectorizer(min_df=2, ngram_range=(1, 2)).fit_transform(texts)
    sims = (x @ x.T).tocoo()
    same_cluster = _cluster_of(data)
    cands = []
    for i, j, v in zip(sims.row, sims.col, sims.data, strict=True):
        if i < j and 0.5 <= v < 0.999:
            a, b = ids[i], ids[j]
            if same_cluster.get(a) is None or same_cluster.get(a) != same_cluster.get(b):
                cands.append((a, b))
    rng.shuffle(cands)
    return [(a, b, "near_text") for a, b in cands[:n]]


async def cmd_sample(out: Path, n_clusters: int, n_near: int, seed: int) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            data = await _load(session)
    finally:
        await engine.dispose()
    rng = random.Random(seed)
    # precision pairs: representative vs each other member, stratified by stage
    by_stage: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for cid, mem in data["members"].items():
        rep = data["clusters"][cid].representative_post_id
        for m in mem:
            if m != rep:
                stage = data["stages"].get(m) or "unknown"
                by_stage[stage].append((rep, m, f"cluster_{stage}"))
    picked: list[tuple[int, int, str]] = []
    per_stage = max(1, n_clusters // max(1, len(by_stage)))
    for pairs in by_stage.values():
        rng.shuffle(pairs)
        picked += pairs[:per_stage]
    picked += _near_miss_phash(data, 7, 12, n_near // 2, rng)
    picked += _near_miss_text(data, n_near - n_near // 2, rng)
    rng.shuffle(picked)

    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)

    def _img(pid: int) -> str | None:
        post = data["posts"][pid][0]
        if not post.media_path:
            return None
        src = settings.media_dir / post.media_path
        if not src.exists():
            return None
        dst = out / "img" / f"{pid}{src.suffix}"
        if not dst.exists():
            shutil.copyfile(src, dst)
        return f"img/{dst.name}"

    rows = []
    parts = [
        f"<title>Dedup labels</title><style>{STYLE}</style>",
        "<h1>Are these two posts the same content?</h1>",
        "<p>Label each pair in <code>labels.csv</code>: <b>dup</b> = same meme/still/text "
        "(re-encoded, cropped, watermarked or re-captioned copies count as dup); <b>not</b> = "
        "different content; <b>unsure</b> if you cannot tell. Ignore which channel posted it.</p>",
    ]
    for k, (a, b, source) in enumerate(picked, 1):
        rows.append([k, a, b, source, ""])
        cells = []
        for pid in (a, b):
            post, topic = data["posts"][pid]
            img = _img(pid)
            text = html.escape(re.sub(r"\s+", " ", post.text)[:400])
            cells.append(
                f'<div class="post"><small>post {pid} · {topic} · {post.posted_at:%Y-%m-%d}</small>'
                + (f'<br><img src="{img}">' if img else "")
                + (f"<pre>{text}</pre>" if text else "")
                + "</div>"
            )
        parts.append(
            f'<div class="pair"><h3 style="grid-column:1/3">#{k} · {source}</h3>'
            f"{cells[0]}{cells[1]}</div>"
        )
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pair", "a", "b", "source", "label"])
        w.writerows(rows)
    (out / "sheet.html").write_text("\n".join(parts), encoding="utf-8")
    counts: dict[str, int] = defaultdict(int)
    for _, _, src in picked:
        counts[src] += 1
    print(f"{len(picked)} pairs -> {out}/labels.csv, sheet.html; by source: {dict(counts)}")


async def cmd_score(directory: Path) -> None:
    labels = list(csv.DictReader((directory / "labels.csv").open(encoding="utf-8")))
    labelled = [r for r in labels if r["label"].strip() in ("dup", "not")]
    if not labelled:
        print("no labels yet")
        return
    engine = make_engine(get_settings().database_url)
    try:
        async with make_session_factory(engine)() as session:
            data = await _load(session)
    finally:
        await engine.dispose()
    cluster_of = _cluster_of(data)
    post_id_of = {pid: pid for pid in data["posts"]}
    # posts of an album map to the first member (clusters are per post)
    first: dict[tuple[int, int], int] = {}
    for pid, (post, _) in sorted(data["posts"].items()):
        first.setdefault(_post_key(post), pid)
    for pid, (post, _) in data["posts"].items():
        post_id_of[pid] = first[_post_key(post)]

    tp = fp = fn = tn = 0
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in labelled:
        a, b = post_id_of[int(r["a"])], post_id_of[int(r["b"])]
        together = cluster_of.get(a) is not None and cluster_of.get(a) == cluster_of.get(b)
        truth = r["label"].strip() == "dup"
        key = ("tp" if truth else "fp") if together else ("fn" if truth else "tn")
        by_source[r["source"]][key] += 1
        tp += key == "tp"
        fp += key == "fp"
        fn += key == "fn"
        tn += key == "tn"
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    print(f"labelled pairs: {len(labelled)} (dup={tp + fn}, not={fp + tn})")
    print(f"pairwise precision = {precision:.3f}  ({tp}/{tp + fp})")
    print(f"pairwise recall (pooled) = {recall:.3f}  ({tp}/{tp + fn})")
    print("| source | tp | fp | fn | tn |\n|---|---|---|---|---|")
    for src, c in sorted(by_source.items()):
        print(f"| {src} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} |")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--out", type=Path, default=Path("data/eval/dedup"))
    s.add_argument("--clusters", type=int, default=90)
    s.add_argument("--near", type=int, default=40)
    s.add_argument("--seed", type=int, default=42)
    c = sub.add_parser("score")
    c.add_argument("--dir", type=Path, default=Path("data/eval/dedup"))
    args = ap.parse_args()
    if args.cmd == "sample":
        asyncio.run(cmd_sample(args.out, args.clusters, args.near, args.seed))
    else:
        asyncio.run(cmd_score(args.dir))


if __name__ == "__main__":
    main()
