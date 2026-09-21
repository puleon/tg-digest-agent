"""Pairwise judging with bias probes (SPEC §8.2): every pair is judged in both orders, so
position bias is the share of verdicts that flip with the order; verbosity bias is how often
the longer side wins beyond what the order-consistent verdicts explain; self-preference is
measured by running the same pairs through a judge of another model family.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from tgdigest.llm.client import LLMClient, LLMOutputError, Usage
from tgdigest.prompts import load_prompt

PAIRWISE_PROMPT = ("pairwise_digest", 1)


class PairVerdict(BaseModel):
    winner: Literal["A", "B"]
    reason: str = Field(default="", max_length=200)


@dataclass
class PairResult:
    pair_id: str
    winner: str | None
    """Winner as the caller labelled the sides (``left`` / ``right``), None when inconsistent."""
    ab: str | None
    ba: str | None
    consistent: bool
    longer: str
    usage: Usage = field(default_factory=Usage)
    error: str | None = None


def render_digest_for_judge(items: Sequence[dict[str, Any]], intro: str = "") -> str:
    lines = [intro] if intro else []
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.get('title', '')} [{it.get('topic')} · @{it.get('channel')}]")
        if it.get("why"):
            lines.append(f"   {it['why']}")
    return "\n".join(lines)


async def _judge_once(
    llm: LLMClient, profile: str, a: str, b: str, *, tier: str
) -> tuple[PairVerdict, Usage]:
    out, comp = await llm.structured(
        [
            {"role": "system", "content": load_prompt(*PAIRWISE_PROMPT)},
            {
                "role": "user",
                "content": (
                    f"Reader profile: {profile}\n\n<<<ISSUE A\n{a}\nISSUE A>>>\n\n"
                    f"<<<ISSUE B\n{b}\nISSUE B>>>"
                ),
            },
        ],
        PairVerdict,
        tier=tier,  # type: ignore[arg-type]
        max_tokens=120,
    )
    return out, comp.usage


async def judge_pair(
    llm: LLMClient, pair_id: str, profile: str, left: str, right: str, *, tier: str = "fast"
) -> PairResult:
    """Judge (left, right) as A/B and then as B/A; map both back to left/right."""
    longer = "left" if len(left) > len(right) else "right"
    try:
        v1, u1 = await _judge_once(llm, profile, left, right, tier=tier)
        v2, u2 = await _judge_once(llm, profile, right, left, tier=tier)
    except LLMOutputError as exc:
        return PairResult(pair_id, None, None, None, False, longer, error=str(exc)[:80])
    first = "left" if v1.winner == "A" else "right"
    second = "left" if v2.winner == "B" else "right"
    consistent = first == second
    return PairResult(
        pair_id,
        first if consistent else None,
        first,
        second,
        consistent,
        longer,
        usage=u1 + u2,
    )


@dataclass
class BiasSummary:
    pairs: int
    judged: int
    position_flip_rate: float
    """Share of judged pairs whose verdict changed with the presentation order."""
    longer_wins_rate: float
    """Among consistent verdicts: share won by the longer side (0.5 = no verbosity bias)."""
    left_wins: int
    right_wins: int


def summarize(results: Sequence[PairResult]) -> BiasSummary:
    judged = [r for r in results if r.error is None]
    consistent = [r for r in judged if r.consistent]
    flips = sum(1 for r in judged if not r.consistent)
    longer_wins = sum(1 for r in consistent if r.winner == r.longer)
    return BiasSummary(
        pairs=len(results),
        judged=len(judged),
        position_flip_rate=flips / len(judged) if judged else float("nan"),
        longer_wins_rate=longer_wins / len(consistent) if consistent else float("nan"),
        left_wins=sum(1 for r in consistent if r.winner == "left"),
        right_wins=sum(1 for r in consistent if r.winner == "right"),
    )


def agreement(a: Sequence[PairResult], b: Sequence[PairResult]) -> tuple[int, float, float]:
    """(pairs both judged consistently, raw agreement, Cohen's kappa) between two judges."""
    by_a = {r.pair_id: r.winner for r in a if r.winner}
    by_b = {r.pair_id: r.winner for r in b if r.winner}
    keys = sorted(set(by_a) & set(by_b))
    if not keys:
        return 0, float("nan"), float("nan")
    agree = sum(by_a[k] == by_b[k] for k in keys) / len(keys)
    pa_left = sum(by_a[k] == "left" for k in keys) / len(keys)
    pb_left = sum(by_b[k] == "left" for k in keys) / len(keys)
    expected = pa_left * pb_left + (1 - pa_left) * (1 - pb_left)
    kappa = (agree - expected) / (1 - expected) if expected < 1 else float("nan")
    return len(keys), agree, kappa
