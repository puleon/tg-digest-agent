"""D6 — hand-labelled pairs for dedup precision/recall (SPEC §6.3 metric).

sample  draw pairs: members of found clusters (precision) + near misses just outside the
        thresholds — pHash distance 7..12, TF-IDF cosine >= 0.5 — (recall); write labels.csv,
        copy images, build sheet.html for the human
score   pairwise precision / recall of the current clusters against labels.csv
render  rebuild sheet.html from the current posts table (all pairs or --pairs 9,37,43)
features per-pair evidence (pHash distance, file/text equality, TF-IDF cosine, image
        contrast, channel/time gap) -> features.csv, to tune thresholds on the labels

uv run python scripts/dedup_labels.py sample --out data/eval/dedup
uv run python scripts/dedup_labels.py score --dir data/eval/dedup
uv run python scripts/dedup_labels.py features --dir data/eval/dedup
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
from tgdigest.dedup.signatures import hamming, text_overlap

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
    post_of = _post_of(data)
    out: set[tuple[int, int]] = set()
    for start in range(0, len(arr), 2048):
        dist = _popcount(arr[start : start + 2048, None] ^ arr[None, :])
        ii, jj = np.nonzero((dist >= lo) & (dist <= hi))
        for i, j in zip(ii, jj, strict=True):
            a, b = int(idx[start + i]), int(idx[j])
            if a >= b or post_of[a] == post_of[b]:  # two images of one album: not a pair
                continue
            if same_cluster.get(a) is None or same_cluster.get(a) != same_cluster.get(b):
                out.add((a, b))
    pairs = sorted(out)
    rng.shuffle(pairs)
    return [(a, b, "near_phash") for a, b in pairs[:n]]


def _cluster_of(data: dict[str, Any]) -> dict[int, int]:
    return {pid: cid for cid, mem in data["members"].items() for pid in mem}


def _post_of(data: dict[str, Any]) -> dict[int, int]:
    """Message id -> post id (the first member of its album; clusters are per post)."""
    first: dict[tuple[int, int], int] = {}
    for pid, (post, _) in sorted(data["posts"].items()):
        first.setdefault(_post_key(post), pid)
    return {pid: first[_post_key(post)] for pid, (post, _) in data["posts"].items()}


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


async def cmd_sample(
    out: Path, n_clusters: int, n_near: int, seed: int, exclude: Path | None = None
) -> None:
    """``exclude``: an earlier labels.csv — posts it touches are left out, so the new sample is
    a hold-out for rules tuned on the old one."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            data = await _load(session)
    finally:
        await engine.dispose()
    rng = random.Random(seed)
    seen: set[int] = set()
    if exclude is not None:
        post_of = _post_of(data)
        for r in csv.DictReader(exclude.open(encoding="utf-8")):
            seen.update(post_of.get(int(r[k]), int(r[k])) for k in ("a", "b"))
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
        picked += [p for p in pairs if p[0] not in seen and p[1] not in seen][:per_stage]
    near = _near_miss_phash(data, 7, 12, n_near, rng)
    near += _near_miss_text(data, n_near, rng)
    picked += [p for p in near if p[0] not in seen and p[1] not in seen][: n_near // 2] + [
        p for p in near[n_near:] if p[0] not in seen and p[1] not in seen
    ][: n_near - n_near // 2]
    rng.shuffle(picked)

    out.mkdir(parents=True, exist_ok=True)
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pair", "a", "b", "source", "label"])
        w.writerows([k, a, b, source, ""] for k, (a, b, source) in enumerate(picked, 1))
    _write_sheet(out, [(k, a, b, src) for k, (a, b, src) in enumerate(picked, 1)], data, settings)
    counts: dict[str, int] = defaultdict(int)
    for _, _, src in picked:
        counts[src] += 1
    print(f"{len(picked)} pairs -> {out}/labels.csv, sheet.html; by source: {dict(counts)}")


def _write_sheet(
    out: Path, pairs: list[tuple[int, int, int, str]], data: dict[str, Any], settings: Any
) -> None:
    """sheet.html for (pair number, a, b, source) rows, from the current posts table."""
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

    parts = [
        f"<title>Dedup labels</title><style>{STYLE}</style>",
        "<h1>Are these two posts the same content?</h1>",
        "<p>Label each pair in <code>labels.csv</code>: <b>dup</b> = same meme/still/text "
        "(re-encoded, cropped, watermarked or re-captioned copies count as dup); <b>not</b> = "
        "different content; <b>unsure</b> if you cannot tell. Ignore which channel posted it.</p>",
    ]
    for k, a, b, source in pairs:
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
    (out / "sheet.html").write_text("\n".join(parts), encoding="utf-8")


async def cmd_render(directory: Path, pairs: str | None) -> None:
    """Rebuild sheet.html from the current posts (after a text repair, say); ``pairs`` limits it
    to a comma-separated list of pair numbers."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            data = await _load(session)
    finally:
        await engine.dispose()
    wanted = {int(x) for x in pairs.split(",")} if pairs else None
    rows = [
        (int(r["pair"]), int(r["a"]), int(r["b"]), r["source"])
        for r in csv.DictReader((directory / "labels.csv").open(encoding="utf-8"))
        if wanted is None or int(r["pair"]) in wanted
    ]
    _write_sheet(directory, rows, data, settings)
    print(f"{len(rows)} pairs -> {directory}/sheet.html")


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
    post_id_of = _post_of(data)

    tp = fp = fn = tn = skipped = 0
    errors: list[str] = []
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in labelled:
        a, b = post_id_of[int(r["a"])], post_id_of[int(r["b"])]
        if a == b:  # two images of one album (older samples): a post cannot duplicate itself
            skipped += 1
            continue
        together = cluster_of.get(a) is not None and cluster_of.get(a) == cluster_of.get(b)
        truth = r["label"].strip() == "dup"
        key = ("tp" if truth else "fp") if together else ("fn" if truth else "tn")
        by_source[r["source"]][key] += 1
        if key in ("fp", "fn"):
            errors.append(f"  {key} #{r['pair']} {r['source']} posts {a}/{b}")
        tp += key == "tp"
        fp += key == "fp"
        fn += key == "fn"
        tn += key == "tn"
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    print(
        f"labelled pairs: {len(labelled)} "
        f"(dup={tp + fn}, not={fp + tn}, same-post skipped={skipped})"
    )
    print(f"pairwise precision = {precision:.3f}  ({tp}/{tp + fp})")
    print(f"pairwise recall (pooled) = {recall:.3f}  ({tp}/{tp + fn})")
    print("| source | tp | fp | fn | tn |\n|---|---|---|---|---|")
    for src, c in sorted(by_source.items()):
        print(f"| {src} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} |")
    if errors:
        print("errors:\n" + "\n".join(errors))


def _image_std(path: Path) -> float | None:
    """Grayscale contrast at 32×32 — the same number ``signatures.is_blank`` thresholds."""
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as img:
            gray = ImageOps.exif_transpose(img).convert("L").resize((32, 32))
            return round(float(np.asarray(gray, dtype=np.float64).std()), 2)
    except (OSError, ValueError):
        return None


async def cmd_features(directory: Path) -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            data = await _load(session)
    finally:
        await engine.dispose()
    posts, sigs = data["posts"], data["sigs"]
    post_of = _post_of(data)
    members: dict[int, list[int]] = defaultdict(list)  # post id -> its message ids
    for pid in sorted(posts):
        members[post_of[pid]].append(pid)
    # how many distinct files share a caption: a caption reused under different media is a
    # rubric template, not a repost
    files_per_text: dict[str, set[str]] = defaultdict(set)
    for s in sigs.values():
        if s.text_hash and s.file_sha256:
            files_per_text[s.text_hash].add(s.file_sha256)
    ids = [pid for pid, s in sigs.items() if s.text_key and pid == post_of[pid]]
    vec = TfidfVectorizer(min_df=2, ngram_range=(1, 2)).fit([sigs[i].text_key for i in ids])
    row_of = {pid: k for k, pid in enumerate(ids)}
    matrix = vec.transform([sigs[i].text_key for i in ids])

    def cosine(a: int, b: int) -> float | None:
        if a in row_of and b in row_of:
            return round(float((matrix[row_of[a]] @ matrix[row_of[b]].T).toarray()[0, 0]), 3)
        return None

    labels = list(csv.DictReader((directory / "labels.csv").open(encoding="utf-8")))
    out_rows = []
    for r in labels:
        a, b = post_of[int(r["a"])], post_of[int(r["b"])]
        pa, pb = posts[a][0], posts[b][0]
        ma, mb = members[a], members[b]
        ph = [
            hamming(sigs[x].phash, sigs[y].phash)
            for x in ma
            for y in mb
            if x in sigs and y in sigs and sigs[x].phash is not None and sigs[y].phash is not None
        ]
        files_a = {sigs[x].file_sha256 for x in ma if x in sigs and sigs[x].file_sha256}
        files_b = {sigs[y].file_sha256 for y in mb if y in sigs and sigs[y].file_sha256}
        ka, kb = (
            (sigs[a].text_key or "") if a in sigs else "",
            (sigs[b].text_key or "") if b in sigs else "",
        )
        std = [
            _image_std(settings.media_dir / p.media_path) if p.media_path else None
            for p in (pa, pb)
        ]
        out_rows.append(
            {
                "pair": r["pair"],
                "source": r["source"],
                "label": r["label"],
                "a": a,
                "b": b,
                "phash_min": min(ph) if ph else "",
                "file_same": int(bool(files_a & files_b)),
                "text_same": int(bool(ka) and ka == kb),
                "text_len_a": len(ka),
                "text_len_b": len(kb),
                "prefix": int(
                    bool(ka) and bool(kb) and ka != kb and (ka.startswith(kb) or kb.startswith(ka))
                ),
                "tfidf": cosine(a, b) if cosine(a, b) is not None else "",
                "jaccard": round(text_overlap(ka, kb), 3) if ka and kb else "",
                "files_per_text": max(
                    len(files_per_text.get(sigs[x].text_hash or "", ()))
                    for x in (a, b)
                    if x in sigs
                ),
                "same_channel": int(pa.channel_id == pb.channel_id),
                "days": abs((pa.posted_at - pb.posted_at).days),
                "std_a": std[0] if std[0] is not None else "",
                "std_b": std[1] if std[1] is not None else "",
            }
        )
    with (directory / "features.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    print(f"{len(out_rows)} pairs -> {directory}/features.csv")


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
    s.add_argument("--exclude", type=Path, help="labels.csv whose posts must not reappear")
    c = sub.add_parser("score")
    c.add_argument("--dir", type=Path, default=Path("data/eval/dedup"))
    f = sub.add_parser("features")
    f.add_argument("--dir", type=Path, default=Path("data/eval/dedup"))
    r = sub.add_parser("render")
    r.add_argument("--dir", type=Path, default=Path("data/eval/dedup"))
    r.add_argument("--pairs", help="comma-separated pair numbers (default: all)")
    args = ap.parse_args()
    if args.cmd == "sample":
        asyncio.run(cmd_sample(args.out, args.clusters, args.near, args.seed, args.exclude))
    elif args.cmd == "score":
        asyncio.run(cmd_score(args.dir))
    elif args.cmd == "features":
        asyncio.run(cmd_features(args.dir))
    else:
        asyncio.run(cmd_render(args.dir, args.pairs))


if __name__ == "__main__":
    main()
