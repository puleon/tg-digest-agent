"""D8 — retrieval evaluation: pooled relevance labels and the ablation table (SPEC §8.1).

pool    run the pooling retrievers (BM25 / dense / hybrid+rerank on the full variant, plus the
        text-only and +OCR hybrid variants at a shallower depth) over queries.yaml; store every
        run in runs.jsonl, the union of results in pool.csv, an empty labels.csv and a blind
        labelling sheet (posts in random order, no retriever shown)
score   recall@20 / nDCG@10 / MRR per configuration from runs.jsonl + labels.csv (0/1/2);
        --topic restricts to one topic, --by-kind breaks down by query kind
judge   grade every pooled (query, post) pair with a local LLM (SPEC §8.2) into
        judge_<tier>.csv — the mass labeller, to be calibrated against the human labels
agree   Cohen's kappa between labels.csv (human) and a judge file, 3-class and binary

uv run python scripts/retrieval_eval.py pool --out data/eval/retrieval
uv run python scripts/retrieval_eval.py judge --dir data/eval/retrieval --tier fast
uv run python scripts/retrieval_eval.py agree --dir data/eval/retrieval --judge judge_fast.csv
uv run python scripts/retrieval_eval.py score --dir data/eval/retrieval --topic humor
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import random
import shutil
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from tgdigest.config import get_settings
from tgdigest.retrieval.bm25 import BM25Index
from tgdigest.retrieval.documents import VARIANTS, passage_for
from tgdigest.retrieval.embeddings import BGEM3Embedder
from tgdigest.retrieval.index import COLLECTION, Hit, PostIndex, SearchFilters
from tgdigest.retrieval.metrics import evaluate, mean
from tgdigest.retrieval.rerank import BGEReranker
from tgdigest.retrieval.search import SearchConfig, run_search

QUERIES = Path("docs/experiments/d8-retrieval/queries.yaml")
FILTERS = SearchFilters(exclude_ads=True, representatives_only=True)  # product defaults

STYLE = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:20px auto;padding:0 12px}
h2{border-top:3px solid #333;padding-top:12px;margin-top:30px}
.post{display:grid;grid-template-columns:260px 1fr;gap:12px;border:1px solid #ddd;
      padding:8px;margin:8px 0}
img{max-width:100%;max-height:260px}pre{white-space:pre-wrap;font-size:13px;margin:0}
small{color:#666}.k{color:#06c;font-weight:bold}
"""


@dataclass
class Run:
    query_id: str
    config: str
    ranked: list[int]
    seconds: float


# --- pooling configurations -------------------------------------------------------------------
POOL_CONFIGS: dict[str, tuple[str, str, int, int]] = {
    # name: (variant, mode, rerank_depth, pool depth)
    "bm25:full": ("full", "bm25", 0, 20),
    "dense:full": ("full", "dense", 0, 20),
    "sparse:full": ("full", "sparse", 0, 10),
    "hybrid:full": ("full", "hybrid", 0, 10),
    "hybrid+rerank:full": ("full", "hybrid", 50, 20),
    "hybrid:text": ("text", "hybrid", 0, 10),
    "hybrid:text_ocr": ("text_ocr", "hybrid", 0, 10),
    "hybrid:text_ocr_caption": ("text_ocr_caption", "hybrid", 0, 10),
    "hybrid+rerank:text": ("text", "hybrid", 50, 10),
    "hybrid+rerank:text_ocr_caption": ("text_ocr_caption", "hybrid", 50, 10),
}
RUN_LIMIT = 20


def _load_queries(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return list(yaml.safe_load(fh))


def _eligible(payload: dict[str, Any]) -> bool:
    """The same predicate FILTERS applies inside Qdrant, for the in-memory BM25 corpus."""
    return payload.get("is_ad") is not True and bool(payload.get("is_representative", True))


def _scroll_payloads(client: QdrantClient) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=1024, offset=offset, with_payload=True, with_vectors=False
        )
        for p in points:
            out[int(p.id)] = dict(p.payload or {})
        if offset is None:
            break
    return out


def cmd_pool(out: Path, queries_path: Path, threads: int | None) -> None:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    index = PostIndex(client, BGEM3Embedder(threads=threads))
    reranker = BGEReranker(threads=threads)
    queries = _load_queries(queries_path)
    payloads = _scroll_payloads(client)
    corpus = {pid: p for pid, p in payloads.items() if _eligible(p)}
    print(f"index: {len(payloads)} posts, {len(corpus)} eligible (no ads, representatives)")
    bm25 = {
        v: BM25Index.build(list(corpus), [passage_for(p, v) for p in corpus.values()])
        for v in VARIANTS
    }

    runs: list[Run] = []
    pool: dict[str, dict[int, dict[str, int]]] = defaultdict(dict)  # qid -> pid -> {config: rank}
    for q in queries:
        for name, (variant, mode, depth, pool_depth) in POOL_CONFIGS.items():
            t0 = time.perf_counter()
            if mode == "bm25":
                ranked = [pid for pid, _ in bm25[variant].search(q["query"], limit=RUN_LIMIT)]
            else:
                cfg = SearchConfig(variant=variant, mode=mode, limit=RUN_LIMIT, rerank_depth=depth)  # type: ignore[arg-type]
                hits: list[Hit] = run_search(
                    index, [q["query"]], config=cfg, filters=FILTERS, reranker=reranker
                )
                ranked = [h.post_id for h in hits]
            runs.append(Run(q["id"], name, ranked, round(time.perf_counter() - t0, 3)))
            for rank, pid in enumerate(ranked[:pool_depth], 1):
                pool[q["id"]].setdefault(pid, {})[name] = rank
        print(f"{q['id']} {q['query'][:50]:<50} pooled={len(pool[q['id']])}")

    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    with (out / "runs.jsonl").open("w", encoding="utf-8") as fh:
        for run in runs:
            fh.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")
    rng = random.Random(8)
    label_rows: list[list[Any]] = []
    parts = [
        f"<title>Retrieval labels</title><style>{STYLE}</style>",
        "<h1>Is this post relevant to the query?</h1>",
        "<p>Grade each pooled post in <code>labels.csv</code>: <b>2</b> = answers the query / is "
        "exactly what was asked; <b>1</b> = partially or loosely related (same topic, a passing "
        "mention); <b>0</b> = not relevant. Posts are in random order; which retriever found "
        "them is hidden. Judge by what you see (picture + text), not by how the system "
        "described it.</p>",
    ]
    for q in queries:
        pids = list(pool[q["id"]])
        rng.shuffle(pids)
        head = f"{q['id']} · {q['topic']} · {q['kind']} · "
        parts.append(f'<h2>{head}<span class="k">{html.escape(q["query"])}</span></h2>')
        for pid in pids:
            p = payloads[pid]
            label_rows.append([q["id"], pid, ""])
            parts.append(
                f'<div class="post"><div>{_img_tag(out, pid, p, settings.media_dir)}'
                f"<small>post {pid} · @{p.get('channel')} · {p.get('date')}</small></div>"
                f"<pre>{html.escape(_display_text(p))}</pre></div>"
            )
    with (out / "pool.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "post_id", "found_by"])
        for qid, posts in pool.items():
            for pid, by in posts.items():
                w.writerow([qid, pid, " ".join(f"{k}@{r}" for k, r in sorted(by.items()))])
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "post_id", "label"])
        w.writerows(label_rows)
    (out / "sheet.html").write_text("\n".join(parts), encoding="utf-8")
    sizes = [len(v) for v in pool.values()]
    print(
        f"{len(queries)} queries, {sum(sizes)} judgments to make (min {min(sizes)}, "
        f"max {max(sizes)}) -> {out}/labels.csv, sheet.html; runs in runs.jsonl"
    )


def _display_text(p: dict[str, Any]) -> str:
    """Post text for the human; OCR/captions are shown separately and marked as model output."""
    sources = p.get("sources") or {}
    parts = [sources.get("text") or p.get("text") or ""]
    if sources.get("ocr"):
        parts.append("[OCR] " + sources["ocr"][:300])
    if sources.get("caption"):
        parts.append("[caption] " + sources["caption"][:200])
    return "\n".join(x for x in parts if x)[:1200]


def _img_tag(out: Path, pid: int, payload: dict[str, Any], media_dir: Path) -> str:
    rel = payload.get("media_path")
    if not rel:
        return ""
    src = media_dir / rel
    if not src.exists():
        return ""
    dst = out / "img" / f"{pid}{src.suffix}"
    if not dst.exists():
        shutil.copyfile(src, dst)
    return f'<img src="img/{dst.name}"><br>'


# --- LLM judge (SPEC §8.2) ----------------------------------------------------------------------
JUDGE_PROMPT = ("judge_relevance", 1)


class Judgment(BaseModel):
    grade: int = Field(ge=0, le=2)
    reason: str = ""


async def cmd_judge(directory: Path, tier: str, concurrency: int, limit: int | None) -> None:
    from tgdigest.llm.client import LLMClient, LLMOutputError
    from tgdigest.prompts import load_prompt

    settings = get_settings()
    llm = LLMClient(settings)
    queries = {q["id"]: q for q in _load_queries(QUERIES)}
    payloads = _scroll_payloads(QdrantClient(url=settings.qdrant_url))
    pairs = [
        (r["query_id"], int(r["post_id"]))
        for r in csv.DictReader((directory / "labels.csv").open(encoding="utf-8"))
    ][:limit]
    out_path = directory / f"judge_{tier}.csv"
    done: dict[tuple[str, int], dict[str, str]] = {}
    if out_path.exists():  # resumable
        for r in csv.DictReader(out_path.open(encoding="utf-8")):
            done[(r["query_id"], int(r["post_id"]))] = r
    todo = [p for p in pairs if p not in done]
    print(f"{len(pairs)} pairs, {len(done)} judged already, {len(todo)} to go ({tier})")
    sem = asyncio.Semaphore(concurrency)
    system = load_prompt(*JUDGE_PROMPT)

    async def one(qid: str, pid: int) -> dict[str, str]:
        text = _display_text(payloads.get(pid, {}))
        user = f"Query: {queries[qid]['query']}\n\n<<<POST\n{text}\nPOST>>>"
        async with sem:
            t0 = time.perf_counter()
            try:
                j, comp = await llm.structured(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    Judgment,
                    tier=tier,  # type: ignore[arg-type]
                    max_tokens=120,
                )
                grade, reason, model = str(j.grade), j.reason, comp.model
            except LLMOutputError as exc:
                grade, reason, model = "", f"error: {str(exc)[:80]}", ""
        return {
            "query_id": qid,
            "post_id": str(pid),
            "grade": grade,
            "reason": reason.replace("\n", " ")[:200],
            "model": model,
            "seconds": f"{time.perf_counter() - t0:.2f}",
        }

    fields = ["query_id", "post_id", "grade", "reason", "model", "seconds"]
    with out_path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if not done:
            w.writeheader()
        for start in range(0, len(todo), 50):
            rows = await asyncio.gather(*(one(q, p) for q, p in todo[start : start + 50]))
            w.writerows(rows)
            fh.flush()
            print(f"  judged {min(start + 50, len(todo))}/{len(todo)}")


def cmd_agree(directory: Path, judge_file: str) -> None:
    from sklearn.metrics import cohen_kappa_score

    human = {
        (r["query_id"], r["post_id"]): int(r["label"])
        for r in csv.DictReader((directory / "labels.csv").open(encoding="utf-8"))
        if r["label"].strip() in ("0", "1", "2")
    }
    judge = {
        (r["query_id"], r["post_id"]): int(r["grade"])
        for r in csv.DictReader((directory / judge_file).open(encoding="utf-8"))
        if r["grade"].strip() in ("0", "1", "2")
    }
    keys = sorted(set(human) & set(judge))
    if not keys:
        print("no overlap between human labels and the judge file")
        return
    h = [human[k] for k in keys]
    j = [judge[k] for k in keys]
    agree = sum(a == b for a, b in zip(h, j, strict=True)) / len(keys)
    kappa3 = cohen_kappa_score(h, j)
    kappa3w = cohen_kappa_score(h, j, weights="linear")
    kappa2 = cohen_kappa_score([int(x >= 1) for x in h], [int(x >= 1) for x in j])
    print(
        f"{len(keys)} pairs judged by both\n"
        f"agreement {agree:.3f} · kappa (3 grades) {kappa3:.3f} · "
        f"weighted kappa {kappa3w:.3f} · kappa (relevant vs not) {kappa2:.3f}"
    )
    conf: dict[tuple[int, int], int] = defaultdict(int)
    for a, b in zip(h, j, strict=True):
        conf[(a, b)] += 1
    print("confusion human→judge (rows: human 0/1/2, cols: judge 0/1/2):")
    for a in (0, 1, 2):
        print(f"  {a}: " + "  ".join(f"{conf[(a, b)]:4d}" for b in (0, 1, 2)))


# --- scoring -----------------------------------------------------------------------------------
def cmd_score(
    directory: Path, topic: str | None, by_kind: bool, queries_path: Path, labels_file: str
) -> None:
    queries = {q["id"]: q for q in _load_queries(queries_path)}
    labels: dict[str, dict[int, int]] = defaultdict(dict)
    for r in csv.DictReader((directory / labels_file).open(encoding="utf-8")):
        grade = (r.get("label") or r.get("grade") or "").strip()
        if grade in ("0", "1", "2"):
            labels[r["query_id"]][int(r["post_id"])] = int(grade)
    runs = [json.loads(line) for line in (directory / "runs.jsonl").open(encoding="utf-8")]
    wanted = {
        qid for qid, q in queries.items() if (topic is None or q["topic"] == topic) and labels[qid]
    }
    if not wanted:
        print("no labelled queries" + (f" for topic {topic}" if topic else ""))
        return
    per_config: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for r in runs:
        if r["query_id"] in wanted:
            per_config[r["config"]].append(
                (r["query_id"], evaluate(r["ranked"], labels[r["query_id"]]))
            )
    n = len(wanted)
    print(
        f"{n} labelled queries"
        + (f" ({topic})" if topic else "")
        + f", {sum(len(v) for q, v in labels.items() if q in wanted)} judgments\n"
    )
    print("| configuration | recall@20 | nDCG@10 | MRR | judged in top-20 |\n|---|---|---|---|---|")
    for name, rows in sorted(per_config.items()):
        ms = [m for _, m in rows]
        print(
            f"| {name} | {mean([m.recall_at_20 for m in ms]):.3f} | "
            f"{mean([m.ndcg_at_10 for m in ms]):.3f} | {mean([m.mrr for m in ms]):.3f} | "
            f"{mean([m.judged_in_top_20 / 20 for m in ms]):.2f} |"
        )
    if by_kind:
        kinds = sorted({queries[q]["kind"] for q in wanted})
        print("\n| configuration | " + " | ".join(f"nDCG@10 {k}" for k in kinds) + " |")
        print("|---|" + "---|" * len(kinds))
        for name, rows in sorted(per_config.items()):
            cells = []
            for k in kinds:
                cells.append(
                    f"{mean([m.ndcg_at_10 for q, m in rows if queries[q]['kind'] == k]):.3f}"
                )
            print(f"| {name} | " + " | ".join(cells) + " |")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pool")
    p.add_argument("--out", type=Path, default=Path("data/eval/retrieval"))
    p.add_argument("--queries", type=Path, default=QUERIES)
    p.add_argument("--threads", type=int, default=None)
    s = sub.add_parser("score")
    s.add_argument("--dir", type=Path, default=Path("data/eval/retrieval"))
    s.add_argument("--queries", type=Path, default=QUERIES)
    s.add_argument("--topic", default=None)
    s.add_argument("--by-kind", action="store_true")
    s.add_argument("--labels", default="labels.csv", help="labels file (human or a judge file)")
    j = sub.add_parser("judge")
    j.add_argument("--dir", type=Path, default=Path("data/eval/retrieval"))
    j.add_argument("--tier", default="fast", choices=["fast", "heavy"])
    j.add_argument("--concurrency", type=int, default=4)
    j.add_argument("--limit", type=int, default=None)
    a = sub.add_parser("agree")
    a.add_argument("--dir", type=Path, default=Path("data/eval/retrieval"))
    a.add_argument("--judge", default="judge_fast.csv")
    args = ap.parse_args()
    if args.cmd == "pool":
        cmd_pool(args.out, args.queries, args.threads)
    elif args.cmd == "judge":
        asyncio.run(cmd_judge(args.dir, args.tier, args.concurrency, args.limit))
    elif args.cmd == "agree":
        cmd_agree(args.dir, args.judge)
    else:
        cmd_score(args.dir, args.topic, args.by_kind, args.queries, args.labels)


if __name__ == "__main__":
    main()
