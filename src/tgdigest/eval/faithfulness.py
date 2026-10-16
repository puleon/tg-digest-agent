"""Faithfulness by atomic claims (SPEC §8.3): split a generated text into claims, check each
one against the source posts → supported / contradicted / unsupported. The metric is the share
of claims that are not supported; ``contradicted`` is the dangerous part of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from tgdigest.llm.client import LLMClient, LLMOutputError, Usage
from tgdigest.prompts import load_prompt

EXTRACT_PROMPT = ("extract_claims", 1)
VERIFY_PROMPT = ("verify_claim", 1)
Verdict = Literal["supported", "contradicted", "unsupported"]


class Claims(BaseModel):
    claims: list[str] = Field(default_factory=list, max_length=20)


class ClaimVerdict(BaseModel):
    verdict: Verdict
    post_id: int | None = None


@dataclass
class CheckedClaim:
    claim: str
    verdict: Verdict
    post_id: int | None


@dataclass
class FaithfulnessReport:
    claims: list[CheckedClaim] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    degraded: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {"supported": 0, "contradicted": 0, "unsupported": 0}
        for c in self.claims:
            out[c.verdict] += 1
        return out

    @property
    def unsupported_share(self) -> float:
        n = len(self.claims)
        return (self.counts["contradicted"] + self.counts["unsupported"]) / n if n else float("nan")


def source_block(posts: Sequence[dict[str, Any]]) -> str:
    parts = []
    for p in posts:
        lines = [f"[post {p['post_id']}] @{p.get('channel')} · {p.get('date')}"]
        for key, tag in (("text", ""), ("ocr", "[OCR] "), ("caption", "[caption] ")):
            if p.get(key):
                lines.append(f"{tag}{str(p[key])[:800]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


async def extract_claims(
    llm: LLMClient, text: str, *, tier: str = "fast"
) -> tuple[list[str], Usage]:
    out, comp = await llm.structured(
        [
            {"role": "system", "content": load_prompt(*EXTRACT_PROMPT)},
            {"role": "user", "content": text.strip()[:4000]},
        ],
        Claims,
        tier=tier,  # type: ignore[arg-type]
        max_tokens=700,
    )
    return [c.strip() for c in out.claims if c.strip()], comp.usage


async def verify_claim(
    llm: LLMClient, claim: str, posts: Sequence[dict[str, Any]], *, tier: str = "fast"
) -> tuple[ClaimVerdict, Usage]:
    out, comp = await llm.structured(
        [
            {"role": "system", "content": load_prompt(*VERIFY_PROMPT)},
            {
                "role": "user",
                "content": f"Claim: {claim}\n\n<<<POSTS\n{source_block(posts)}\nPOSTS>>>",
            },
        ],
        ClaimVerdict,
        tier=tier,  # type: ignore[arg-type]
        max_tokens=60,
    )
    return out, comp.usage


async def faithfulness(
    llm: LLMClient,
    text: str,
    posts: Sequence[dict[str, Any]],
    *,
    tier: str = "fast",
    concurrency: int = 2,
) -> FaithfulnessReport:
    report = FaithfulnessReport()
    try:
        claims, usage = await extract_claims(llm, text, tier=tier)
    except LLMOutputError as exc:
        report.degraded.append(f"extract:{str(exc)[:60]}")
        return report
    report.usage = report.usage + usage
    if not claims:
        report.degraded.append("no_claims")
        return report
    sem = asyncio.Semaphore(concurrency)

    async def one(claim: str) -> CheckedClaim:
        async with sem:
            try:
                v, u = await verify_claim(llm, claim, posts, tier=tier)
            except LLMOutputError as exc:
                report.degraded.append(f"verify:{str(exc)[:60]}")
                return CheckedClaim(claim, "unsupported", None)
            report.usage = report.usage + u
            return CheckedClaim(claim, v.verdict, v.post_id)

    report.claims = list(await asyncio.gather(*(one(c) for c in claims)))
    return report
