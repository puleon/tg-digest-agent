from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from pydantic import BaseModel

from tgdigest.digest.crew import (
    DigestDraft,
    DigestItem,
    Pick,
    Plan,
    Problem,
    StoryVector,
    Verdict,
    compose,
    coverage_gaps,
    curate,
    make_plan,
    mechanical_check,
)
from tgdigest.llm.client import Completion, LLMOutputError, Usage


def test_plan_gives_every_topic_a_slot_and_follows_weights() -> None:
    plan = make_plan({"humor": 0.7, "cinema": 0.2, "scifi": 0.1}, minutes=10)
    assert plan.items == 10 and sum(plan.per_topic.values()) == 10
    assert plan.per_topic["humor"] > plan.per_topic["cinema"] >= plan.per_topic["scifi"] >= 1
    assert make_plan({}, minutes=3).items == 5 and make_plan({}, minutes=40).items == 12
    even = make_plan({}, minutes=6).per_topic
    assert sorted(even.values()) == [2, 2, 2]


def _pick(
    pid: int,
    topic: str = "humor",
    ch: int = 1,
    score: float = 0.5,
    fresh: bool = True,
    cluster: int | None = None,
) -> Pick:
    return Pick(
        pid,
        topic,
        f"c{ch}",
        ch,
        score,
        fresh,
        cluster,
        f"text {pid}",
        url=f"u{pid}",
        date="2026-09-20",
    )


def test_curator_respects_quotas_channels_clusters_freshness_and_history() -> None:
    plan = Plan(items=5, per_topic={"humor": 3, "cinema": 1, "scifi": 1}, fresh_share=0.6)
    cands = [
        _pick(1, "humor", 1, 0.9),
        _pick(2, "humor", 1, 0.8),
        _pick(3, "humor", 1, 0.7),  # channel cap 2
        _pick(4, "humor", 2, 0.6, fresh=False),
        _pick(5, "humor", 2, 0.5, cluster=7),
        _pick(6, "humor", 3, 0.4, cluster=7),  # same cluster as 5: dropped
        _pick(7, "cinema", 4, 0.95),
        _pick(8, "cinema", 4, 0.9),  # only one cinema slot
        _pick(9, "scifi", 5, 0.3, fresh=False),
        _pick(10, "scifi", 6, 0.2, fresh=False),
    ]
    picks = curate(cands, plan, exclude=frozenset({1}))
    ids = [p.post_id for p in picks]
    assert 1 not in ids  # shown before
    assert ids.count(7) == 1 and 8 not in ids  # cinema quota
    assert sum(1 for p in picks if p.channel_id == 1) <= 2
    assert not (5 in ids and 6 in ids)  # one per cluster
    assert len(picks) == 5 and sum(p.fresh for p in picks) == 3  # 60 % fresh, then timeless
    assert coverage_gaps(picks, plan) == {}
    starved = curate([_pick(1, "humor")], plan)
    assert coverage_gaps(starved, plan) == {"humor": 2, "cinema": 1, "scifi": 1}


def _story(angle: float, words: dict[int, float]) -> StoryVector | None:
    """A unit vector ``angle`` radians off the x axis: cosine between two = cos(difference)."""
    return StoryVector.of(np.array([np.cos(angle), np.sin(angle), 0.0]), words)


def test_one_story_from_several_channels_takes_one_slot() -> None:
    """The owner's issue of 2026-10-30 carried one short film three times: three channels,
    three texts, no dedup cluster (independent coverage is not a repost)."""
    names = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}  # shared names: "Mama Bear", the director, ...
    other = {11: 1.0, 12: 1.0, 13: 1.0, 14: 1.0}
    near, mid, far = np.arccos(0.85), np.arccos(0.72), np.arccos(0.5)
    plan = Plan(items=3, per_topic={"humor": 0, "cinema": 3, "scifi": 0}, fresh_share=1.0)
    film = [
        _pick(1, "cinema", 1, 0.9),
        _pick(2, "cinema", 2, 0.8),
        _pick(3, "cinema", 3, 0.7),
        _pick(4, "cinema", 4, 0.6),
        _pick(5, "cinema", 5, 0.5),
    ]
    stories = [
        _story(0.0, names),
        _story(near, other),  # cosine 0.85: one story whatever the words
        _story(mid, names | {5: 1.0}),  # 0.72 and the same names: one story
        _story(mid, other),  # 0.72, nothing named in common: another film's trailer
        _story(-far, names),  # 0.5 off the first, further off the rest
    ]
    cands = [replace(p, story=st) for p, st in zip(film, stories, strict=True)]
    assert [p.post_id for p in curate(cands, plan)] == [1, 4, 5]
    # an image-only post has no text to compare: never a repeat, of anything
    assert StoryVector.of(np.array([1.0, 0.0, 0.0]), {1: 1.0}) is None
    bare = [replace(p, story=None) for p in cands[1:3]]
    assert [p.post_id for p in curate([cands[0], *bare], plan)] == [1, 2, 3]


@dataclass
class CrewLLM:
    """Editor drafts from the posts; the critic complains on the first round if told so."""

    reject_first: bool = False
    fail: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def model_for(self, tier: str) -> str:
        return f"{tier}-model"

    async def structured(
        self, messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        self.calls.append(schema.__name__)
        comp = Completion(text="{}", model="fast-model", usage=Usage(200, 50))
        if schema.__name__ in self.fail:
            raise LLMOutputError("bad json", "raw")
        user = messages[-1]["content"]
        if schema is DigestDraft:
            ids = [
                int(line.split("]")[0][6:])
                for line in user.split("\n")
                if line.startswith("[post ")
            ]
            fixed = "rejected the previous draft" in messages[0]["content"]
            items = [
                DigestItem(
                    post_id=i,
                    title=f"Пост {i}" + (" (испр.)" if fixed else ""),
                    why=f"потому что {i}",
                )
                for i in ids
            ]
            return DigestDraft(intro=f"{len(items)} пунктов", items=items), comp
        if schema is Verdict:
            if self.reject_first and self.calls.count("Verdict") == 1:
                return Verdict(
                    ok=False, problems=[Problem(post_id=2, kind="spoiler", fix="убери финал")]
                ), comp
            return Verdict(ok=True), comp
        raise AssertionError(schema)


def _llm(llm: CrewLLM) -> Any:
    return llm  # the crew only needs ``structured``; typed as LLMClient in the signature


async def test_compose_accepts_a_clean_draft_in_one_round() -> None:
    llm = CrewLLM()
    picks = [_pick(1), _pick(2, "cinema", 2), _pick(3, "scifi", 3)]
    plan = Plan(items=3, per_topic={"humor": 1, "cinema": 1, "scifi": 1})
    res = await compose(_llm(llm), picks, plan)
    assert [it["post_id"] for it in res.items] == [1, 2, 3] and res.intro == "3 пунктов"
    assert res.critic_iterations == 1 and res.critic_problems == [] and res.gaps == {}
    assert res.items[0]["url"] == "u1" and res.items[1]["topic"] == "cinema"
    assert res.usage.total == 500 and llm.calls == ["DigestDraft", "Verdict"]


async def test_critic_rejection_sends_the_draft_back_once() -> None:
    llm = CrewLLM(reject_first=True)
    picks = [_pick(1), _pick(2, "cinema", 2)]
    res = await compose(
        _llm(llm), picks, Plan(items=2, per_topic={"humor": 1, "cinema": 1, "scifi": 0})
    )
    assert res.critic_iterations == 2 and res.critic_problems == []
    assert llm.calls == ["DigestDraft", "Verdict", "DigestDraft", "Verdict"]
    assert res.items[1]["title"].endswith("(испр.)")


async def test_critic_loop_is_bounded_and_failures_degrade() -> None:
    always_no = CrewLLM(reject_first=True)

    async def structured(
        messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        out = await CrewLLM.structured(always_no, messages, schema, **kw)
        if schema is Verdict:
            return Verdict(ok=False, problems=[Problem(post_id=1, kind="clickbait")]), out[1]
        return out

    always_no.structured = structured  # type: ignore[method-assign]
    res = await compose(
        _llm(always_no), [_pick(1)], Plan(items=1, per_topic={"humor": 1, "cinema": 0, "scifi": 0})
    )
    assert res.critic_iterations == 2 and res.critic_problems[0]["kind"] == "clickbait"
    assert len(res.items) == 1  # the last draft is published with the complaint recorded

    broken = CrewLLM(fail={"DigestDraft"})
    res = await compose(
        _llm(broken), [_pick(1)], Plan(items=1, per_topic={"humor": 1, "cinema": 0, "scifi": 0})
    )
    assert res.degraded == ["editor:bad json", "bare_items"] and res.items[0]["title"] == "text 1"
    empty = await compose(
        _llm(broken), [], Plan(items=1, per_topic={"humor": 1, "cinema": 0, "scifi": 0})
    )
    assert empty.items == [] and empty.degraded == ["empty"] and empty.gaps == {"humor": 1}


def test_mechanical_check_catches_missing_repeated_and_invented_posts() -> None:
    picks = [_pick(1), _pick(2)]
    draft = DigestDraft(
        items=[
            DigestItem(post_id=1, title="a", why=""),
            DigestItem(post_id=1, title="b", why=""),
            DigestItem(post_id=9, title="c", why=""),
        ]
    )
    problems = mechanical_check(draft, picks)
    assert (
        any("repeated" in p for p in problems)
        and any("[2]" in p for p in problems)
        and any("[9]" in p for p in problems)
    )
