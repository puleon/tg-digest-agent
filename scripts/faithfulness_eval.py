"""D16 — faithfulness of generated text by atomic claims (SPEC §8.3).

answers  run queries (docs/experiments/d8-retrieval/queries.yaml, --limit) through the agent,
         split each answer into claims, verify each against the posts the answer was written
         from; writes claims.csv (for a human check) and prints the share of unsupported /
         contradicted claims by mode and topic
digest   the same for one digest issue: each item's title + reason against its source post
score    re-read claims.csv (human column filled in) and report judge-vs-human agreement on
         the claim verdicts (Cohen's kappa, 3 classes and supported-vs-not)

uv run python scripts/faithfulness_eval.py answers --limit 12 --out data/eval/faithfulness
uv run python scripts/faithfulness_eval.py digest --user 0 --out data/eval/faithfulness
uv run python scripts/faithfulness_eval.py score --out data/eval/faithfulness
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from tgdigest.config import get_settings
from tgdigest.eval.faithfulness import FaithfulnessReport, faithfulness
from tgdigest.retrieval.metrics import mean

QUERIES = Path("docs/experiments/d8-retrieval/queries.yaml")
FIELDS = ["run_id", "kind", "topic", "mode", "claim", "verdict", "post_id", "human"]


def _write_claims(path: Path, rows: list[dict[str, Any]]) -> None:
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def _rows(
    run_id: str, kind: str, topic: str, mode: str, report: FaithfulnessReport
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "kind": kind,
            "topic": topic,
            "mode": mode,
            "claim": c.claim,
            "verdict": c.verdict,
            "post_id": c.post_id or "",
            "human": "",
        }
        for c in report.claims
    ]


async def cmd_answers(out: Path, limit: int, topic: str | None, rerank: bool) -> None:
    from tgdigest.service import open_services

    settings = get_settings()
    queries = [
        q
        for q in yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
        if not topic or q["topic"] == topic
    ]
    # spread over topics and kinds: every 40 // limit-th query
    step = max(1, len(queries) // limit)
    queries = queries[::step][:limit]
    out.mkdir(parents=True, exist_ok=True)
    services = await open_services(settings, rerank=rerank)
    summary: list[dict[str, Any]] = []
    try:
        for q in queries:
            t0 = time.perf_counter()
            state = await services.ask(q["query"])
            posts = [
                h
                for h in (state.get("relevant") or state.get("graded") or [])
                if int(h["post_id"]) in set(state.get("citations") or [])
                or not state.get("citations")
            ]
            report = await faithfulness(services.llm, state.get("answer", ""), posts)
            _write_claims(
                out / "claims.csv",
                _rows(q["id"], "answer", q["topic"], str(state.get("mode")), report),
            )
            row = {
                "id": q["id"],
                "topic": q["topic"],
                "mode": state.get("mode"),
                "claims": len(report.claims),
                **report.counts,
                "unsupported_share": report.unsupported_share,
                "answer_chars": len(state.get("answer", "")),
                "seconds": round(time.perf_counter() - t0, 1),
                "agent_usage": dict(state.get("usage") or {}),
                "agent_steps": [
                    {k: s.get(k) for k in ("step", "seconds", "prompt_tokens", "completion_tokens")}
                    for s in state.get("steps") or []
                ],
                "check_usage": {
                    "prompt_tokens": report.usage.prompt_tokens,
                    "completion_tokens": report.usage.completion_tokens,
                },
                "degraded": report.degraded + list(state.get("degraded") or []),
            }
            summary.append(row)
            with (out / "answers.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({**row, "answer": state.get("answer", "")}, ensure_ascii=False)
                    + "\n"
                )
            print(
                f"{q['id']} {state.get('mode')!s:<8} claims={len(report.claims):2d} "
                f"{report.counts} unsupported={report.unsupported_share:.2f} {row['seconds']:.0f}s"
            )
    finally:
        await services.close()
    _print_summary(summary)


async def cmd_digest(out: Path, user: int, days: int) -> None:
    from tgdigest.service import open_services

    settings = get_settings()
    out.mkdir(parents=True, exist_ok=True)
    services = await open_services(settings, rerank=False)
    try:
        result, _ = await services.digest(user, days=days, store=False)
        by_id = {p.post_id: p for p in result.picks}
        summary: list[dict[str, Any]] = []
        for it in result.items:
            pick = by_id[int(it["post_id"])]
            text = f"{it['title']}. {it['why']}"
            posts = [
                {
                    "post_id": pick.post_id,
                    "channel": pick.channel,
                    "date": pick.date,
                    "text": pick.text,
                    "ocr": pick.ocr,
                    "caption": pick.caption,
                }
            ]
            report = await faithfulness(services.llm, text, posts)
            _write_claims(
                out / "claims.csv",
                _rows(f"digest:{it['post_id']}", "digest", it["topic"], "digest", report),
            )
            summary.append(
                {
                    "id": it["post_id"],
                    "topic": it["topic"],
                    "mode": "digest",
                    "claims": len(report.claims),
                    **report.counts,
                    "unsupported_share": report.unsupported_share,
                }
            )
            print(
                f"post {it['post_id']:>7} {it['topic']:<7} claims={len(report.claims)} "
                f"{report.counts}"
            )
    finally:
        await services.close()
    _print_summary(summary)


def _print_summary(summary: list[dict[str, Any]]) -> None:
    if not summary:
        return
    total = sum(r["claims"] for r in summary)
    unsup = sum(r["unsupported"] for r in summary)
    contra = sum(r["contradicted"] for r in summary)
    share = (unsup + contra) / total if total else float("nan")
    print(
        f"\n{len(summary)} texts, {total} claims: supported {total - unsup - contra}, "
        f"unsupported {unsup}, contradicted {contra} → unsupported share {share:.3f} "
        f"(contradicted {contra / total if total else 0:.3f})"
    )
    by: dict[str, list[float]] = defaultdict(list)
    for r in summary:
        by[f"mode={r['mode']}"].append(r["unsupported_share"])
        by[f"topic={r['topic']}"].append(r["unsupported_share"])
    print("| group | texts | mean unsupported share |\n|---|---|---|")
    for k, v in sorted(by.items()):
        print(f"| {k} | {len(v)} | {mean(v):.3f} |")


def cmd_score(out: Path) -> None:
    from sklearn.metrics import cohen_kappa_score

    rows = [
        r for r in csv.DictReader((out / "claims.csv").open(encoding="utf-8")) if r["human"].strip()
    ]
    if not rows:
        print(
            "no human verdicts in claims.csv yet "
            "(fill the `human` column: supported / contradicted / unsupported)"
        )
        return
    j = [r["verdict"] for r in rows]
    h = [r["human"].strip().lower() for r in rows]
    agree = sum(a == b for a, b in zip(j, h, strict=True)) / len(rows)
    k3 = cohen_kappa_score(h, j)
    k2 = cohen_kappa_score([x == "supported" for x in h], [x == "supported" for x in j])
    print(
        f"{len(rows)} claims checked by a human · agreement {agree:.3f} · "
        f"kappa (3 classes) {k3:.3f} · "
        f"kappa (supported vs not) {k2:.3f}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("answers")
    a.add_argument("--out", type=Path, default=Path("data/eval/faithfulness"))
    a.add_argument("--limit", type=int, default=12)
    a.add_argument("--topic", default=None)
    a.add_argument("--no-rerank", action="store_true")
    d = sub.add_parser("digest")
    d.add_argument("--out", type=Path, default=Path("data/eval/faithfulness"))
    d.add_argument("--user", type=int, default=0)
    d.add_argument("--days", type=int, default=7)
    s = sub.add_parser("score")
    s.add_argument("--out", type=Path, default=Path("data/eval/faithfulness"))
    args = ap.parse_args()
    if args.cmd == "answers":
        asyncio.run(cmd_answers(args.out, args.limit, args.topic, not args.no_rerank))
    elif args.cmd == "digest":
        asyncio.run(cmd_digest(args.out, args.user, args.days))
    else:
        cmd_score(args.out)
