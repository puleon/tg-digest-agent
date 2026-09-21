"""Digest crew (SPEC §6.7): Planner → Curator → Editor → Critic, at most two critic rounds.

The Planner and the Curator are code, not prompts: composition constraints (reading budget,
topic coverage, ≤ 2 posts per channel, fresh/timeless balance, nothing shown before) are
exact rules and are checked exactly. The Editor and the Critic are LLM steps with versioned
prompts; the Critic's mechanical checks (duplicates inside the issue, repeats of earlier
issues, plan coverage) are code too — only hallucination / spoiler / clickbait need a reader.
``critic_iterations`` is recorded per issue as the pipeline's maturity metric.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from tgdigest.llm.client import LLMClient, LLMOutputError, Usage
from tgdigest.profile.model import TOPICS
from tgdigest.prompts import load_prompt

log = structlog.get_logger(__name__)

EDITOR_PROMPT = ("digest_editor", 1)
CRITIC_PROMPT = ("digest_critic", 1)
MAX_CRITIC_ROUNDS = 2
FRESH_DAYS = 3


# --- plan -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    items: int
    per_topic: dict[str, int]
    max_per_channel: int = 2
    fresh_share: float = 0.6
    """Share of items from the last FRESH_DAYS days; the rest are "timeless" older posts."""
    window_days: int = 7

    def to_json(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "per_topic": self.per_topic,
            "max_per_channel": self.max_per_channel,
            "fresh_share": self.fresh_share,
            "window_days": self.window_days,
        }


def make_plan(topic_weights: dict[str, float], *, minutes: int = 10, window_days: int = 7) -> Plan:
    """Reading budget ≈ one item per minute, 5–12 items; every topic gets at least one slot,
    the rest follow the profile's topic weights."""
    items = max(5, min(12, minutes))
    weights = {t: max(float(topic_weights.get(t, 0.0)), 0.0) for t in TOPICS}
    total = sum(weights.values()) or 1.0
    per_topic = {t: 1 for t in TOPICS}
    remaining = items - len(TOPICS)
    shares = {t: weights[t] / total * remaining for t in TOPICS}
    for t in TOPICS:
        per_topic[t] += int(shares[t])
    left = items - sum(per_topic.values())
    for t in sorted(TOPICS, key=lambda t: shares[t] - int(shares[t]), reverse=True)[:left]:
        per_topic[t] += 1
    return Plan(items=items, per_topic=per_topic, window_days=window_days)


# --- curate ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Pick:
    post_id: int
    topic: str
    channel: str
    channel_id: int
    score: float
    fresh: bool
    cluster_id: int | None
    text: str
    ocr: str = ""
    caption: str = ""
    url: str = ""
    date: str = ""


def curate(
    candidates: Sequence[Pick], plan: Plan, *, exclude: frozenset[int] = frozenset()
) -> list[Pick]:
    """Greedy fill of the plan by score: topic quotas, channel cap, fresh/timeless balance, no
    duplicates (one post per cluster), nothing from ``exclude`` (shown before)."""
    chosen: list[Pick] = []
    per_topic: dict[str, int] = dict.fromkeys(TOPICS, 0)
    per_channel: dict[int, int] = {}
    clusters: set[int] = set()
    taken: set[int] = set()
    fresh_target = round(plan.items * plan.fresh_share)

    def admissible(p: Pick, *, fresh_needed: bool | None) -> bool:
        if p.post_id in exclude or p.post_id in taken or p.cluster_id in clusters:
            return False
        if per_topic.get(p.topic, 0) >= plan.per_topic.get(p.topic, 0):
            return False
        if per_channel.get(p.channel_id, 0) >= plan.max_per_channel:
            return False
        return fresh_needed is None or p.fresh == fresh_needed

    ranked = sorted(candidates, key=lambda p: (-p.score, p.post_id))
    for stage_fresh in (True, False, None):  # fresh first, then timeless, then whatever is left
        for p in ranked:
            if len(chosen) >= plan.items:
                break
            if stage_fresh is True and sum(c.fresh for c in chosen) >= fresh_target:
                break
            if not admissible(p, fresh_needed=stage_fresh):
                continue
            chosen.append(p)
            taken.add(p.post_id)
            per_topic[p.topic] = per_topic.get(p.topic, 0) + 1
            per_channel[p.channel_id] = per_channel.get(p.channel_id, 0) + 1
            if p.cluster_id is not None:
                clusters.add(p.cluster_id)
    return chosen


def coverage_gaps(picks: Sequence[Pick], plan: Plan) -> dict[str, int]:
    """Topic slots the curator could not fill (a real shortfall, reported, not hidden)."""
    have: dict[str, int] = dict.fromkeys(TOPICS, 0)
    for p in picks:
        have[p.topic] = have.get(p.topic, 0) + 1
    return {t: plan.per_topic[t] - have[t] for t in TOPICS if have[t] < plan.per_topic[t]}


# --- edit + critique ------------------------------------------------------------------------------
class DigestItem(BaseModel):
    post_id: int
    title: str = Field(max_length=120)
    why: str = Field(max_length=400)


class DigestDraft(BaseModel):
    intro: str = Field(default="", max_length=300)
    items: list[DigestItem]


class Problem(BaseModel):
    post_id: int
    kind: Literal["hallucination", "spoiler", "clickbait"]
    fix: str = Field(default="", max_length=200)


class Verdict(BaseModel):
    ok: bool
    problems: list[Problem] = Field(default_factory=list)


@dataclass
class DigestResult:
    plan: Plan
    picks: list[Pick]
    intro: str
    items: list[dict[str, Any]]
    critic_iterations: int
    critic_problems: list[dict[str, Any]]
    gaps: dict[str, int]
    usage: Usage
    seconds: float
    degraded: list[str] = field(default_factory=list)


def _post_block(p: Pick) -> str:
    parts = [f"[post {p.post_id}] @{p.channel} · {p.date} · {p.topic}"]
    if p.text:
        parts.append(p.text[:1200])
    if p.ocr:
        parts.append(f"[OCR] {p.ocr[:300]}")
    if p.caption:
        parts.append(f"[caption] {p.caption[:200]}")
    return "\n".join(parts)


async def edit(
    llm: LLMClient, picks: Sequence[Pick], *, feedback: str = "", tier: str = "fast"
) -> tuple[DigestDraft, Usage]:
    system = load_prompt(*EDITOR_PROMPT).format(
        feedback=f"\n\nThe reviewer rejected the previous draft: {feedback}" if feedback else ""
    )
    blocks = "\n\n".join(_post_block(p) for p in picks)
    draft, comp = await llm.structured(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"<<<POSTS\n{blocks}\nPOSTS>>>"},
        ],
        DigestDraft,
        tier=tier,  # type: ignore[arg-type]
        max_tokens=200 + 120 * len(picks),
    )
    return draft, comp.usage


async def critique(
    llm: LLMClient, picks: Sequence[Pick], draft: DigestDraft, *, tier: str = "fast"
) -> tuple[Verdict, Usage]:
    by_id = {p.post_id: p for p in picks}
    pairs = "\n\n".join(
        f"ITEM post {it.post_id}\ntitle: {it.title}\nwhy: {it.why}\nSOURCE:\n"
        + _post_block(by_id[it.post_id])
        for it in draft.items
        if it.post_id in by_id
    )
    verdict, comp = await llm.structured(
        [
            {"role": "system", "content": load_prompt(*CRITIC_PROMPT)},
            {"role": "user", "content": f"<<<DIGEST\n{pairs}\nDIGEST>>>"},
        ],
        Verdict,
        tier=tier,  # type: ignore[arg-type]
        max_tokens=400,
    )
    return verdict, comp.usage


def mechanical_check(draft: DigestDraft, picks: Sequence[Pick]) -> list[str]:
    """What code can verify: one entry per pick, no invented ids, no duplicated ids."""
    ids = [it.post_id for it in draft.items]
    problems = []
    if len(set(ids)) != len(ids):
        problems.append("an item is repeated")
    missing = [p.post_id for p in picks if p.post_id not in ids]
    if missing:
        problems.append(f"posts left out: {missing}")
    invented = [i for i in ids if i not in {p.post_id for p in picks}]
    if invented:
        problems.append(f"unknown posts: {invented}")
    return problems


async def compose(
    llm: LLMClient,
    picks: Sequence[Pick],
    plan: Plan,
    *,
    tier: str = "fast",
    max_rounds: int = MAX_CRITIC_ROUNDS,
) -> DigestResult:
    t0 = time.perf_counter()
    usage = Usage()
    degraded: list[str] = []
    feedback = ""
    rounds = 0
    draft = DigestDraft(intro="", items=[])
    problems: list[dict[str, Any]] = []
    if not picks:
        return DigestResult(
            plan, [], "", [], 0, [], coverage_gaps(picks, plan), usage, 0.0, ["empty"]
        )
    while True:
        try:
            draft, u = await edit(llm, picks, feedback=feedback, tier=tier)
            usage = usage + u
        except LLMOutputError as exc:
            degraded.append(f"editor:{str(exc)[:60]}")
            break
        mech = mechanical_check(draft, picks)
        if rounds >= max_rounds:
            break
        try:
            verdict, u = await critique(llm, picks, draft, tier=tier)
            usage = usage + u
        except LLMOutputError as exc:
            degraded.append(f"critic:{str(exc)[:60]}")
            break
        rounds += 1
        problems = [p.model_dump() for p in verdict.problems]
        if verdict.ok and not mech:
            break
        feedback = "; ".join(
            [f"post {p.post_id}: {p.kind} — {p.fix}" for p in verdict.problems] + mech
        )
        log.info("digest_critic_rejected", round=rounds, feedback=feedback[:300])
    if not draft.items:  # the editor failed: publish the picks bare, say so
        draft = DigestDraft(
            intro="",
            items=[
                DigestItem(post_id=p.post_id, title=p.text[:60] or f"post {p.post_id}", why="")
                for p in picks
            ],
        )
        degraded.append("bare_items")
    by_id = {p.post_id: p for p in picks}
    items = [
        {
            "post_id": it.post_id,
            "title": it.title,
            "why": it.why,
            "topic": by_id[it.post_id].topic,
            "channel": by_id[it.post_id].channel,
            "url": by_id[it.post_id].url,
            "date": by_id[it.post_id].date,
            "fresh": by_id[it.post_id].fresh,
        }
        for it in draft.items
        if it.post_id in by_id
    ]
    return DigestResult(
        plan=plan,
        picks=list(picks),
        intro=draft.intro,
        items=items,
        critic_iterations=rounds,
        critic_problems=problems,
        gaps=coverage_gaps(picks, plan),
        usage=usage,
        seconds=round(time.perf_counter() - t0, 2),
        degraded=degraded,
    )
