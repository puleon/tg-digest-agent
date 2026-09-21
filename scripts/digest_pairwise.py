"""D15/D16 — pairwise: interest-ranked digest vs the views baseline, judged in both orders
(SPEC §6.6 comparison, §8.2 judge biases).

pairs   build N pairs for a user — the same past week and reading budget, one issue ranked by
        the v1 interest score, one by views (both go through the same editor/critic) —
        and store them in pairs.jsonl; nothing is saved as the user's issue
judge   judge every pair with a tier (fast = Qwen3.6, heavy = gpt-oss-120b) in both orders →
        verdicts_<tier>.jsonl; prints wins, position-flip rate, longer-wins rate, seconds
compare report agreement (raw, kappa) between the two tiers' verdicts (self-preference probe:
        the fast judge shares a family with the editor, the heavy one does not)

uv run python scripts/digest_pairwise.py pairs --user 0 --weeks 8 --out data/eval/pairwise
uv run python scripts/digest_pairwise.py judge --tier fast --out data/eval/pairwise
uv run python scripts/digest_pairwise.py judge --tier heavy --out data/eval/pairwise
uv run python scripts/digest_pairwise.py compare --out data/eval/pairwise
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgdigest.config import get_settings
from tgdigest.eval.pairwise import (
    PairResult,
    agreement,
    judge_pair,
    render_digest_for_judge,
    summarize,
)
from tgdigest.llm.client import LLMClient


async def cmd_pairs(out: Path, user: int, weeks: int, minutes: list[int]) -> None:
    from tgdigest.service import open_services

    settings = get_settings()
    out.mkdir(parents=True, exist_ok=True)
    services = await open_services(settings, rerank=False)
    try:
        profile = await services.profile(user)
        profile_text = (
            ", ".join(f"{t}: {w:.2f}" for t, w in sorted((profile.topic_weights or {}).items()))
            + f"; исключения: {profile.negative_prefs or '—'}"
            if profile
            else "профиль пуст (равные веса тем)"
        )
        with (out / "pairs.jsonl").open("w", encoding="utf-8") as fh:
            for w in range(weeks):
                until = datetime.now(UTC) - timedelta(days=7 * w)
                for m in minutes:
                    t0 = time.perf_counter()
                    v1, _ = await services.digest_at(
                        user, minutes=m, days=7, until=until, baseline=False
                    )
                    base, _ = await services.digest_at(
                        user, minutes=m, days=7, until=until, baseline=True
                    )
                    if not v1.items or not base.items:
                        print(f"week -{w} minutes {m}: empty issue, skipped")
                        continue
                    row = {
                        "pair_id": f"w{w}_m{m}",
                        "until": until.isoformat(),
                        "minutes": m,
                        "profile": profile_text,
                        "left": {
                            "ranking": "v1",
                            "intro": v1.intro,
                            "items": v1.items,
                            "critic_iterations": v1.critic_iterations,
                        },
                        "right": {
                            "ranking": "views",
                            "intro": base.intro,
                            "items": base.items,
                            "critic_iterations": base.critic_iterations,
                        },
                        "seconds": round(time.perf_counter() - t0, 1),
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    overlap = {i["post_id"] for i in v1.items} & {i["post_id"] for i in base.items}
                    print(
                        f"week -{w} minutes {m}: v1 {len(v1.items)} items, "
                        f"views {len(base.items)} items, overlap {len(overlap)}, "
                        f"{row['seconds']:.0f}s"
                    )
    finally:
        await services.close()


async def cmd_judge(out: Path, tier: str) -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    pairs = [json.loads(line) for line in (out / "pairs.jsonl").open(encoding="utf-8")]
    results: list[PairResult] = []
    t0 = time.perf_counter()
    with (out / f"verdicts_{tier}.jsonl").open("w", encoding="utf-8") as fh:
        for p in pairs:
            left = render_digest_for_judge(p["left"]["items"], p["left"]["intro"])
            right = render_digest_for_judge(p["right"]["items"], p["right"]["intro"])
            r = await judge_pair(llm, p["pair_id"], p["profile"], left, right, tier=tier)
            results.append(r)
            fh.write(json.dumps({**r.__dict__, "usage": r.usage.total}, ensure_ascii=False) + "\n")
            print(
                f"{p['pair_id']:<8} ab={r.ab} ba={r.ba} winner={r.winner} "
                f"longer={r.longer} {r.error or ''}"
            )
    s = summarize(results)
    elapsed = time.perf_counter() - t0
    print(
        f"\n{tier} judge: {s.judged}/{s.pairs} judged · v1 wins {s.left_wins} · "
        f"views wins {s.right_wins} · position flips {s.position_flip_rate:.2f} · "
        f"longer side wins {s.longer_wins_rate:.2f} · "
        f"{elapsed / max(1, 2 * s.judged):.1f} s per verdict, {elapsed:.0f} s total"
    )


def cmd_compare(out: Path) -> None:
    def load(tier: str) -> list[PairResult]:
        path = out / f"verdicts_{tier}.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.open(encoding="utf-8"):
            d = json.loads(line)
            d.pop("usage", None)
            rows.append(PairResult(**d))
        return rows

    fast, heavy = load("fast"), load("heavy")
    n, agree, kappa = agreement(fast, heavy)
    print(
        f"fast vs heavy judge: {n} pairs judged consistently by both · "
        f"agreement {agree:.2f} · kappa {kappa:.2f}"
    )
    for name, rs in (("fast", fast), ("heavy", heavy)):
        if rs:
            s = summarize(rs)
            print(
                f"  {name}: v1 wins {s.left_wins}, views wins {s.right_wins}, "
                f"flips {s.position_flip_rate:.2f}, longer wins {s.longer_wins_rate:.2f}"
            )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pairs")
    p.add_argument("--out", type=Path, default=Path("data/eval/pairwise"))
    p.add_argument("--user", type=int, default=0)
    p.add_argument("--weeks", type=int, default=8)
    p.add_argument("--minutes", type=int, nargs="+", default=[6, 10])
    j = sub.add_parser("judge")
    j.add_argument("--out", type=Path, default=Path("data/eval/pairwise"))
    j.add_argument("--tier", default="fast", choices=["fast", "heavy"])
    c = sub.add_parser("compare")
    c.add_argument("--out", type=Path, default=Path("data/eval/pairwise"))
    args = ap.parse_args()
    if args.cmd == "pairs":
        asyncio.run(cmd_pairs(args.out, args.user, args.weeks, args.minutes))
    elif args.cmd == "judge":
        asyncio.run(cmd_judge(args.out, args.tier))
    else:
        cmd_compare(args.out)
