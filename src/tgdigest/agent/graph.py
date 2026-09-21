"""Search agent as a LangGraph state machine (SPEC §6.5).

::

    route → rewrite → retrieve → grade → { synthesize
                                          | rewrite (≤ 2, few relevant)
                                          | broaden (nothing relevant: drop filters, widen)
                                          | verify_external (research mode, before synthesis)
                                          | answer_with_caveat (budget exhausted) }

Every LLM call and tool call is a step in ``state["steps"]`` (name, seconds, usage), so a run
is inspectable without Langfuse and the tracer only mirrors it. Budgets: ``max_iterations``
retrieval rounds and ``max_tokens`` across all LLM calls; either one exhausted → caveat.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from tgdigest.agent.guardrails import check_output, quarantine
from tgdigest.agent.tools import ToolRegistry, ToolResult
from tgdigest.agent.tracing import StepHandle, Tracer, current_run
from tgdigest.ingest.injection import injection_score
from tgdigest.llm.client import LLMClient, LLMOutputError, Usage
from tgdigest.prompts import load_prompt, prompt_id
from tgdigest.retrieval.rewrite import rewrite_query

log = structlog.get_logger(__name__)

Mode = Literal["search", "news", "research"]
ROUTE_PROMPT = ("route_query", 1)
GRADE_PROMPT = ("grade_context", 1)
SYNTH_PROMPT = ("synthesize_answer", 1)

MAX_REWRITES = 2
GRADE_DEPTH = 10
ENOUGH_RELEVANT = {"search": 3, "news": 3, "research": 2}
ENOUGH_SHARE = 0.3
"""Fewer than ENOUGH_RELEVANT hits still count as enough when they are a good share of what was
graded — a narrow query over a small corpus should not be re-phrased into noise."""


class Route(BaseModel):
    mode: Mode = "search"
    topic: Literal["scifi", "humor", "cinema"] | None = None
    days: int | None = Field(default=None, ge=1, le=365)
    exclude_spoilers: bool = False
    reason: str = ""


class Grade(BaseModel):
    post_id: int
    relevant: bool
    reason: str = Field(default="", max_length=80)
    """Kept short on purpose: ten reasons at decode speed cost more than the retrieval."""


class Grades(BaseModel):
    grades: list[Grade]


class AgentState(TypedDict, total=False):
    # input
    query: str
    user_id: int
    # routing
    mode: Mode
    topic: str | None
    days: int | None
    exclude_spoilers: bool
    route_reason: str
    # retrieval loop
    queries: list[str]
    rewrites: int
    iteration: int
    broadened: bool
    hits: list[dict[str, Any]]
    graded: list[dict[str, Any]]
    relevant: list[dict[str, Any]]
    verification: list[dict[str, Any]]
    quarantined: list[dict[str, Any]]
    # output
    answer: str
    citations: list[int]
    caveat: str | None
    degraded: list[str]
    steps: list[dict[str, Any]]
    usage: dict[str, int]
    trace_id: str | None


@dataclass
class AgentDeps:
    llm: LLMClient
    tools: ToolRegistry
    synthesis_tier: Literal["fast", "heavy"] = "fast"
    max_iterations: int = 6
    max_tokens: int = 40_000
    max_items: int = 8
    rewrite: bool = True
    tracer: Tracer | None = None
    guard: bool = True
    """Injection defenses (quarantine + output checks); off only to measure their effect."""


def _begin(name: str, kind: str, input: Any = None) -> tuple[float, StepHandle | None]:
    """Start timing a step and open its observation on the current trace (if any)."""
    run = current_run.get()
    return time.perf_counter(), (run.step(name, kind=kind, input=input) if run else None)


def _step(
    state: AgentState,
    name: str,
    t0: float,
    usage: Usage | None = None,
    *,
    obs: StepHandle | None = None,
    model: str | None = None,
    **info: Any,
) -> None:
    entry: dict[str, Any] = {"step": name, "seconds": round(time.perf_counter() - t0, 3), **info}
    if usage is not None:
        entry["prompt_tokens"] = usage.prompt_tokens
        entry["completion_tokens"] = usage.completion_tokens
        state["usage"]["prompt_tokens"] += usage.prompt_tokens
        state["usage"]["completion_tokens"] += usage.completion_tokens
    state.setdefault("steps", []).append(entry)
    if obs is not None:
        tokens = (
            {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
            if usage
            else None
        )
        obs.end(output=info, usage=tokens, model=model)


def _tokens(state: AgentState) -> int:
    u = state.get("usage") or {}
    return int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0))


def _fuse(lists: list[list[dict[str, Any]]], k: int = 60) -> list[dict[str, Any]]:
    score: dict[int, float] = {}
    first: dict[int, dict[str, Any]] = {}
    for hits in lists:
        for h in hits:
            pid = int(h["post_id"])
            score[pid] = score.get(pid, 0.0) + 1.0 / (k + int(h["rank"]))
            first.setdefault(pid, h)
    order = sorted(score, key=lambda pid: (-score[pid], first[pid]["rank"]))
    return [
        {**first[pid], "rank": r + 1, "score": round(score[pid], 4)} for r, pid in enumerate(order)
    ]


def _post_block(h: dict[str, Any]) -> str:
    parts = [f"[post {h['post_id']}] @{h.get('channel')} · {h.get('date')} · {h.get('topic')}"]
    if h.get("text"):
        parts.append(h["text"])
    if h.get("ocr"):
        parts.append(f"[OCR] {h['ocr']}")
    if h.get("caption"):
        parts.append(f"[caption] {h['caption']}")
    return "\n".join(parts)


def build_agent_graph(deps: AgentDeps) -> Any:
    llm = deps.llm

    async def route(state: AgentState) -> AgentState:
        t0, obs = _begin("route", "generation", state["query"])
        base: AgentState = {
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "steps": [],
            "degraded": [],
            "rewrites": 0,
            "iteration": 0,
            "broadened": False,
            "verification": [],
            "quarantined": [],
            "citations": [],
            "caveat": None,
        }
        messages = [
            {"role": "system", "content": load_prompt(*ROUTE_PROMPT)},
            {"role": "user", "content": state["query"]},
        ]
        try:
            r, comp = await llm.structured(messages, Route, tier="fast", max_tokens=150)  # type: ignore[arg-type]
            usage = comp.usage
        except LLMOutputError as exc:
            r = Route(mode="search", reason="router failed; defaulted to search")
            usage = None
            base["degraded"].append(f"route:{str(exc)[:60]}")
        days = r.days if r.days is not None else (7 if r.mode == "news" else None)
        out: AgentState = {
            **base,
            "mode": r.mode,
            "topic": r.topic,
            "days": days,
            "exclude_spoilers": r.exclude_spoilers,
            "route_reason": r.reason,
            "queries": [state["query"]],
        }
        _step(
            out,
            "route",
            t0,
            usage,
            obs=obs,
            model=llm.model_for("fast"),
            mode=r.mode,
            topic=r.topic,
            days=days,
            prompt=prompt_id(*ROUTE_PROMPT),
        )
        return out

    async def rewrite(state: AgentState) -> AgentState:
        t0, obs = _begin("rewrite", "generation", state["query"])
        if not deps.rewrite:
            _step(state, "rewrite", t0, obs=obs, skipped=True)
            return state
        rw = await rewrite_query(llm, state["query"])
        queries = list(dict.fromkeys([*rw.queries, state["query"]]))[:3]
        if rw.degraded:
            state["degraded"].append("rewrite:failed")
        _step(state, "rewrite", t0, obs=obs, queries=queries, prompt=rw.prompt)
        return {**state, "queries": queries}

    async def retrieve(state: AgentState) -> AgentState:
        t0, obs = _begin(
            "retrieve",
            "retriever",
            {"queries": state["queries"], "topic": state.get("topic"), "days": state.get("days")},
        )
        args_base: dict[str, Any] = {
            "limit": 20,
            "exclude_spoilers": state.get("exclude_spoilers", False),
        }
        if state.get("topic"):
            args_base["topic"] = state["topic"]
        days = state.get("days")
        if days:
            args_base["date_from"] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        lists: list[list[dict[str, Any]]] = []
        failures: list[str] = []
        for q in state["queries"]:
            res: ToolResult = await deps.tools.call("search_index", {**args_base, "query": q})
            if res.ok:
                lists.append(list(res.data["hits"]))
            else:
                failures.append(f"search_index:{res.error_kind}")
        hits = _fuse(lists) if lists else []
        degraded = state["degraded"] + failures
        quarantined = list(state.get("quarantined") or [])
        if deps.guard and hits:
            held = quarantine(hits)
            if held.dropped:
                hits = held.kept
                quarantined += held.dropped
                degraded.append(f"injection:quarantined:{len(held.dropped)}")
        _step(
            state,
            "retrieve",
            t0,
            obs=obs,
            queries=len(state["queries"]),
            hits=len(hits),
            failures=len(failures),
            filters=args_base,
        )
        return {
            **state,
            "hits": hits,
            "degraded": degraded,
            "quarantined": quarantined,
            "iteration": state["iteration"] + 1,
        }

    async def grade(state: AgentState) -> AgentState:
        t0, obs = _begin(
            "grade", "generation", {"query": state["query"], "hits": len(state["hits"])}
        )
        head = state["hits"][:GRADE_DEPTH]
        if not head:
            _step(state, "grade", t0, obs=obs, graded=0, relevant=0)
            return {**state, "graded": [], "relevant": []}
        blocks = "\n\n".join(_post_block(h) for h in head)
        messages = [
            {"role": "system", "content": load_prompt(*GRADE_PROMPT)},
            {
                "role": "user",
                "content": f"Request: {state['query']}\n\n<<<POSTS\n{blocks}\nPOSTS>>>",
            },
        ]
        try:
            g, comp = await llm.structured(messages, Grades, tier="fast", max_tokens=600)  # type: ignore[arg-type]
            verdict = {x.post_id: x for x in g.grades}
            usage = comp.usage
        except LLMOutputError as exc:  # no grader: trust the ranking, say so
            verdict = {}
            usage = None
            state["degraded"].append(f"grade:{str(exc)[:60]}")
        graded = []
        for h in head:
            v = verdict.get(int(h["post_id"]))
            graded.append(
                {**h, "relevant": v.relevant if v else h["rank"] <= 3, "why": v.reason if v else ""}
            )
        relevant = [h for h in graded if h["relevant"]]
        _step(
            state,
            "grade",
            t0,
            usage,
            obs=obs,
            model=llm.model_for("fast"),
            graded=len(graded),
            relevant=len(relevant),
            prompt=prompt_id(*GRADE_PROMPT),
        )
        return {**state, "graded": graded, "relevant": relevant}

    def decide(state: AgentState) -> str:
        n = len(state.get("relevant") or [])
        graded = len(state.get("graded") or [])
        enough = n >= ENOUGH_RELEVANT[state["mode"]] or (
            n >= 1 and graded > 0 and n / graded >= ENOUGH_SHARE
        )
        if state["iteration"] >= deps.max_iterations or _tokens(state) >= deps.max_tokens:
            return "caveat"
        if enough:
            return (
                "verify"
                if state["mode"] == "research" and not state["verification"]
                else "synthesize"
            )
        if n == 0 and not state["broadened"] and (state.get("topic") or state.get("days")):
            return "broaden"
        if state["rewrites"] < MAX_REWRITES:
            return "requery"
        return (
            "verify"
            if state["mode"] == "research" and n and not state["verification"]
            else ("synthesize" if n else "caveat")
        )

    async def requery(state: AgentState) -> AgentState:
        """Ask for different phrasings, telling the model what did not work."""
        t0, obs = _begin("rewrite_query", "generation", state["queries"])
        seen = ", ".join(state["queries"])
        rw = await rewrite_query(
            llm, f"{state['query']}\n(эти формулировки не нашли нужного: {seen}; предложи другие)"
        )
        fresh = [q for q in rw.queries if q not in state["queries"]][:2] or [state["query"]]
        _step(state, "rewrite_query", t0, obs=obs, queries=fresh)
        return {**state, "queries": fresh, "rewrites": state["rewrites"] + 1}

    async def broaden(state: AgentState) -> AgentState:
        t0, obs = _begin(
            "broaden", "span", {"topic": state.get("topic"), "days": state.get("days")}
        )
        _step(
            state,
            "broaden",
            t0,
            obs=obs,
            dropped={"topic": state.get("topic"), "days": state.get("days")},
        )
        return {**state, "topic": None, "days": None, "broadened": True}

    async def verify(state: AgentState) -> AgentState:
        t0, obs = _begin("verify_external", "tool", state["query"])
        notes: list[dict[str, Any]] = []
        res = await deps.tools.call("web_search", {"query": state["query"][:200], "lang": "ru"})
        if res.ok and res.data["results"]:
            top = res.data["results"][0]
            notes.append(
                {
                    "source": res.data["source"],
                    "title": top["title"],
                    "snippet": top["snippet"],
                    "url": top["url"],
                }
            )
            page = await deps.tools.call("fetch_url", {"url": top["url"]})
            if page.ok:
                text = page.data["text"][:2000]
                flagged, _ = injection_score(text) if deps.guard else (False, [])
                if flagged:
                    state["degraded"].append("injection:page_quarantined")
                    notes[-1]["text"] = "(страница скрыта: содержит инструкции для модели)"
                else:
                    notes[-1]["text"] = text
            else:
                state["degraded"].append(f"fetch_url:{page.error_kind}")
        else:
            state["degraded"].append(f"web_search:{res.error_kind or 'empty'}")
        _step(state, "verify_external", t0, obs=obs, sources=len(notes))
        return {**state, "verification": notes}

    async def synthesize(state: AgentState, caveat: str | None = None) -> AgentState:
        t0, obs = _begin(
            "synthesize",
            "generation",
            {"mode": state["mode"], "posts": len(state.get("relevant") or [])},
        )
        posts = state.get("relevant") or state.get("graded") or state.get("hits") or []
        posts = posts[: deps.max_items * 2]
        blocks = "\n\n".join(_post_block(h) for h in posts) or "(ничего не найдено)"
        extra = ""
        if state.get("verification"):
            extra = (
                "\n\n<<<EXTERNAL\n"
                + "\n\n".join(
                    f"[source: {n['title']}] {n.get('text') or n.get('snippet', '')}"
                    for n in state["verification"]
                )
                + "\nEXTERNAL>>>"
            )
        messages = [
            {
                "role": "system",
                "content": load_prompt(*SYNTH_PROMPT).format(max_items=deps.max_items),
            },
            {
                "role": "user",
                "content": (
                    f"Mode: {state['mode']}\nRequest: {state['query']}\n\n"
                    f"<<<POSTS\n{blocks}\nPOSTS>>>{extra}"
                ),
            },
        ]
        try:
            comp = await llm.complete(messages, tier=deps.synthesis_tier, max_tokens=900)  # type: ignore[arg-type]
            answer, usage = comp.text.strip(), comp.usage
        except LLMOutputError as exc:
            answer, usage = "", None
            state["degraded"].append(f"synthesize:{str(exc)[:60]}")
        if not answer:
            answer = "Не удалось сформулировать ответ. Найденные посты: " + ", ".join(
                f"[post {h['post_id']}]" for h in posts[: deps.max_items]
            )
        if deps.guard and answer:
            sources = [_post_block(h) for h in posts] + [
                str(n.get("text") or "") + " " + str(n.get("url") or "")
                for n in state.get("verification") or []
            ]
            answer, notes = check_output(answer, sources)
            state["degraded"].extend(notes)
        if caveat:
            answer = f"{caveat}\n\n{answer}"
        cited = sorted({int(h["post_id"]) for h in posts if f"[post {h['post_id']}]" in answer})
        _step(
            state,
            "synthesize",
            t0,
            usage,
            obs=obs,
            model=llm.model_for(deps.synthesis_tier),
            tier=deps.synthesis_tier,
            posts=len(posts),
            citations=len(cited),
            prompt=prompt_id(*SYNTH_PROMPT),
        )
        return {**state, "answer": answer, "citations": cited, "caveat": caveat}

    async def answer_with_caveat(state: AgentState) -> AgentState:
        reason = (
            "бюджет шагов исчерпан"
            if state["iteration"] >= deps.max_iterations
            else "бюджет токенов исчерпан"
            if _tokens(state) >= deps.max_tokens
            else "по запросу нашлось мало подходящего"
        )
        return await synthesize(state, caveat=f"⚠️ Ответ неполный: {reason}.")

    graph: StateGraph[AgentState] = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("rewrite", rewrite)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("requery", requery)
    graph.add_node("broaden", broaden)
    graph.add_node("verify", verify)
    graph.add_node("synthesize", synthesize)
    graph.add_node("caveat", answer_with_caveat)
    graph.add_edge(START, "route")
    graph.add_edge("route", "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", decide)
    graph.add_edge("requery", "retrieve")
    graph.add_edge("broaden", "retrieve")
    graph.add_edge("verify", "synthesize")
    graph.add_edge("synthesize", END)
    graph.add_edge("caveat", END)
    return graph.compile()


async def run_agent(deps: AgentDeps, query: str, *, user_id: int = 0) -> AgentState:
    graph = build_agent_graph(deps)
    t0 = time.perf_counter()
    run = deps.tracer.start_run("agent", input=query, user_id=str(user_id)) if deps.tracer else None
    token = current_run.set(run)
    try:
        state: AgentState = await graph.ainvoke({"query": query, "user_id": user_id})
    finally:
        current_run.reset(token)
    if run is not None:
        run.end(
            output=state.get("answer"),
            metadata={
                "mode": state.get("mode"),
                "iterations": state.get("iteration"),
                "tokens": _tokens(state),
                "degraded": state.get("degraded"),
                "citations": state.get("citations"),
            },
        )
        state["trace_id"] = run.trace_id
    log.info(
        "agent_done",
        mode=state.get("mode"),
        iterations=state.get("iteration"),
        steps=[s["step"] for s in state.get("steps", [])],
        tokens=_tokens(state),
        seconds=round(time.perf_counter() - t0, 2),
        degraded=state.get("degraded"),
    )
    return state
