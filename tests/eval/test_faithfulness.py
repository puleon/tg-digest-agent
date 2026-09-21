from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from tgdigest.eval.faithfulness import Claims, ClaimVerdict, faithfulness, source_block
from tgdigest.eval.pairwise import PairResult, PairVerdict, agreement, judge_pair, summarize
from tgdigest.llm.client import Completion, LLMOutputError, Usage


@dataclass
class ClaimLLM:
    """Extracts one claim per sentence; a claim is supported when its words appear in a post."""

    fail_extract: bool = False
    calls: int = 0

    async def structured(
        self, messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        self.calls += 1
        comp = Completion(text="{}", model="fast-model", usage=Usage(50, 10))
        user = messages[-1]["content"]
        if schema is Claims:
            if self.fail_extract:
                raise LLMOutputError("bad", "raw")
            return Claims(claims=[s.strip() for s in user.split(".") if s.strip()]), comp
        if schema is ClaimVerdict:
            claim = user.split("\n")[0].removeprefix("Claim: ").lower()
            posts = user.split("<<<POSTS")[1].lower()
            if "не " in claim and claim.replace("не ", "") in posts:
                return ClaimVerdict(verdict="contradicted", post_id=1), comp
            words = [w for w in claim.split() if len(w) > 3 and not w.startswith("[post")]
            ok = words and all(w in posts for w in words)
            return ClaimVerdict(
                verdict="supported" if ok else "unsupported", post_id=1 if ok else None
            ), comp
        raise AssertionError(schema)


async def test_faithfulness_counts_supported_contradicted_and_unsupported() -> None:
    posts = [
        {
            "post_id": 1,
            "channel": "sf",
            "date": "2026-09-01",
            "text": "Нолан снял Одиссею",
            "ocr": "премьера июль",
        }
    ]
    llm: Any = ClaimLLM()
    report = await faithfulness(
        llm, "Нолан снял Одиссею [post 1]. Премьера июль. Бюджет миллиард.", posts
    )
    assert [c.verdict for c in report.claims] == ["supported", "supported", "unsupported"]
    assert report.counts == {"supported": 2, "contradicted": 0, "unsupported": 1}
    assert abs(report.unsupported_share - 1 / 3) < 1e-9 and report.usage.total == 4 * 60
    assert "[post 1]" in source_block(posts) and "[OCR] премьера июль" in source_block(posts)
    broken = await faithfulness(ClaimLLM(fail_extract=True), "текст.", posts)  # type: ignore[arg-type]
    assert broken.claims == [] and broken.degraded == ["extract:bad"]


@dataclass
class PairLLM:
    """Prefers the longer issue when ``verbose``; prefers position A when ``positional``."""

    verbose: bool = False
    positional: bool = False
    calls: list[str] = field(default_factory=list)

    async def structured(
        self, messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        user = messages[-1]["content"]
        a = user.split("<<<ISSUE A\n")[1].split("\nISSUE A>>>")[0]
        b = user.split("<<<ISSUE B\n")[1].split("\nISSUE B>>>")[0]
        self.calls.append(f"{a[:1]}{b[:1]}")
        comp = Completion(text="{}", model="fast-model", usage=Usage(80, 10))
        if self.positional:
            return PairVerdict(winner="A"), comp
        if self.verbose:
            return PairVerdict(winner="A" if len(a) >= len(b) else "B"), comp
        return PairVerdict(winner="A" if "лучше" in a else "B"), comp


async def test_pairwise_judge_runs_both_orders_and_measures_biases() -> None:
    fair: Any = PairLLM()
    r = await judge_pair(fair, "p1", "любит кино", "выпуск лучше", "выпуск хуже и длиннее")
    assert r.consistent and r.winner == "left" and r.longer == "right" and r.usage.total == 180
    assert fair.calls == ["вв", "вв"]  # both orders were asked
    biased: Any = PairLLM(positional=True)
    r2 = await judge_pair(biased, "p2", "любит кино", "x", "y")
    assert not r2.consistent and r2.winner is None and r2.ab == "left" and r2.ba == "right"
    verbose: Any = PairLLM(verbose=True)
    r3 = await judge_pair(verbose, "p3", "любит кино", "короткий", "длинный длинный длинный")
    assert r3.consistent and r3.winner == "right" == r3.longer

    s = summarize([r, r2, r3])
    assert (s.pairs, s.judged, s.left_wins, s.right_wins) == (3, 3, 1, 1)
    assert abs(s.position_flip_rate - 1 / 3) < 1e-9 and s.longer_wins_rate == 0.5
    other = [
        PairResult("p1", "left", "left", "left", True, "right"),
        PairResult("p3", "left", "left", "left", True, "right"),
    ]
    n, agree, kappa = agreement([r, r2, r3], other)
    assert n == 2 and agree == 0.5 and kappa <= 0.0
