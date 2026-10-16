from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from tgdigest.agent.graph import AgentDeps, Grade, Grades, Route, run_agent
from tgdigest.agent.tools import Tool, ToolError, ToolRegistry
from tgdigest.llm.client import Completion, LLMOutputError, Usage
from tgdigest.retrieval.rewrite import RewrittenQuery

CORPUS: list[dict[str, Any]] = [
    {
        "post_id": 1,
        "channel": "memes",
        "topic": "humor",
        "date": "2026-09-01",
        "text": "кот и дедлайн",
        "ocr": "",
        "caption": "",
    },
    {
        "post_id": 2,
        "channel": "memes",
        "topic": "humor",
        "date": "2026-09-02",
        "text": "",
        "ocr": "ДЕДЛАЙН ГОРИТ",
        "caption": "кот у ноутбука",
    },
    {
        "post_id": 3,
        "channel": "memes",
        "topic": "humor",
        "date": "2026-09-03",
        "text": "понедельник",
        "ocr": "",
        "caption": "",
    },
    {
        "post_id": 4,
        "channel": "sf",
        "topic": "scifi",
        "date": "2026-09-04",
        "text": "Лем о дедлайнах писателя",
        "ocr": "",
        "caption": "",
    },
    {
        "post_id": 5,
        "channel": "films",
        "topic": "cinema",
        "date": "2026-09-05",
        "text": "трейлер",
        "ocr": "",
        "caption": "",
    },
]


@dataclass
class ScriptedLLM:
    """Route / rewrite / grade / synthesize answers driven by simple rules; records calls."""

    route: Route = field(default_factory=lambda: Route(mode="search", topic="humor"))
    phrasings: list[str] = field(default_factory=lambda: ["мем про дедлайн"])
    keywords: list[str] = field(default_factory=list)
    relevant_word: str = "дедлайн"
    fail: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def model_for(self, tier: str) -> str:
        return f"{tier}-model"

    async def structured(
        self, messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        self.calls.append(schema.__name__)
        comp = Completion(text="{}", model="fast-model", usage=Usage(100, 20))
        if schema.__name__ in self.fail:
            raise LLMOutputError("bad json", "raw")
        if schema is Route:
            return self.route, comp
        if schema is RewrittenQuery:
            user = messages[-1]["content"]
            if "предложи другие" in user:
                return RewrittenQuery(queries=["горящие сроки"], keywords=[]), comp
            return RewrittenQuery(queries=self.phrasings, keywords=self.keywords), comp
        if schema is Grades:
            user = messages[-1]["content"]
            grades = []
            for line in user.split("\n"):
                if line.startswith("[post "):
                    pid = int(line.split("]")[0][6:])
                    grades.append(Grade(post_id=pid, relevant=False))
            for g in grades:
                block = _block_of(user, g.post_id)
                g.relevant = self.relevant_word in block.lower()
            return Grades(grades=grades), comp
        raise AssertionError(schema)

    async def complete(self, messages: Any, **kw: Any) -> Completion:
        self.calls.append("complete")
        if "complete" in self.fail:
            raise LLMOutputError("empty", "")
        user = messages[-1]["content"]
        cited = [line.split("]")[0] + "]" for line in user.split("\n") if line.startswith("[post ")]
        text = "Вот что нашлось: " + " ".join(cited[:3])
        if "<<<EXTERNAL" in user:
            text += " [source: Википедия]"
        return Completion(text=text, model=kw.get("tier", "fast") + "-model", usage=Usage(300, 80))


def _deps(llm: ScriptedLLM, tools: ToolRegistry, **kw: Any) -> AgentDeps:
    return AgentDeps(llm=llm, tools=tools, **kw)  # type: ignore[arg-type]


def _block_of(user: str, pid: int) -> str:
    start = user.index(f"[post {pid}]")
    end = user.find("\n\n", start)
    return user[start : end if end > 0 else len(user)]


class SearchArgs(BaseModel):
    query: str
    topic: str | None = None
    date_from: str | None = None
    exclude_spoilers: bool = False
    limit: int = 20


class Q(BaseModel):
    query: str = ""
    lang: str = "ru"
    url: str = ""


def _tools(
    *, search_fails: bool = False, calls: list[dict[str, Any]] | None = None
) -> ToolRegistry:
    calls = calls if calls is not None else []

    async def search_index(args: BaseModel) -> Any:
        a = SearchArgs.model_validate(args.model_dump())
        calls.append({"tool": "search_index", **a.model_dump()})
        if search_fails:
            raise ToolError("unavailable", "qdrant down")
        words = set(a.query.lower().split())
        hits: list[dict[str, Any]] = []
        for p in CORPUS:
            if a.topic and p["topic"] != a.topic:
                continue
            blob = f"{p['text']} {p['ocr']} {p['caption']}".lower()
            score = sum(w in blob for w in words)
            if score:
                hits.append({**p, "score": score})
        hits.sort(key=lambda h: (-int(h["score"]), int(h["post_id"])))
        return {
            "query": a.query,
            "hits": [{**h, "rank": i + 1} for i, h in enumerate(hits[: a.limit])],
            "total": len(hits),
        }

    async def web_search(args: BaseModel) -> Any:
        calls.append({"tool": "web_search", "args": args.model_dump()})
        return {
            "source": "ru.wikipedia.org",
            "results": [
                {
                    "title": "Дедлайн",
                    "snippet": "срок",
                    "url": "https://ru.wikipedia.org/wiki/Дедлайн",
                }
            ],
        }

    async def fetch_url(args: BaseModel) -> Any:
        calls.append({"tool": "fetch_url"})
        return {
            "url": "https://ru.wikipedia.org/wiki/Дедлайн",
            "title": "Дедлайн",
            "text": "Дедлайн — крайний срок.",
        }

    return ToolRegistry(
        [
            Tool("search_index", "", SearchArgs, search_index),
            Tool("web_search", "", Q, web_search),
            Tool("fetch_url", "", Q, fetch_url),
        ]
    )


async def test_search_happy_path_routes_rewrites_retrieves_grades_and_cites() -> None:
    llm = ScriptedLLM(phrasings=["мем про дедлайн", "дедлайн горит"])
    calls: list[dict[str, Any]] = []
    state = await run_agent(_deps(llm, _tools(calls=calls)), "что-нибудь смешное про дедлайны")
    assert [s["step"] for s in state["steps"]] == [
        "route",
        "rewrite",
        "retrieve",
        "grade",
        "synthesize",
    ]
    assert state["mode"] == "search" and state["topic"] == "humor"
    assert {c["query"] for c in calls} == {
        "мем про дедлайн",
        "дедлайн горит",
        "что-нибудь смешное про дедлайны",
    }
    assert all(c["topic"] == "humor" for c in calls)  # the router's topic became a filter
    assert [h["post_id"] for h in state["relevant"]] == [1, 2]  # 4 is scifi: filtered out
    assert state["citations"] == [1, 2] and state["caveat"] is None
    assert state["usage"]["prompt_tokens"] > 0 and state["iteration"] == 1


async def test_few_relevant_triggers_rewrites_then_a_caveat() -> None:
    llm = ScriptedLLM(
        route=Route(mode="search", topic=None),
        phrasings=["понедельник"],
        relevant_word="ничего-такого",
    )
    state = await run_agent(_deps(llm, _tools()), "мемы про понедельник")
    steps = [s["step"] for s in state["steps"]]
    assert steps.count("rewrite_query") == 2 and steps.count("retrieve") == 3
    assert steps[-1] == "synthesize" and state["caveat"] and "мало подходящего" in state["caveat"]
    assert state["rewrites"] == 2 and state["answer"].startswith("⚠️")


async def test_nothing_relevant_broadens_the_filters_once() -> None:
    llm = ScriptedLLM(route=Route(mode="news", topic="cinema", days=7), phrasings=["дедлайн"])
    calls: list[dict[str, Any]] = []
    state = await run_agent(_deps(llm, _tools(calls=calls)), "что нового про дедлайны")
    steps = [s["step"] for s in state["steps"]]
    assert (
        "broaden" in steps
        and state["broadened"]
        and state["topic"] is None
        and state["days"] is None
    )
    assert calls[0]["topic"] == "cinema" and calls[0]["date_from"] is not None
    assert calls[-1]["topic"] is None and calls[-1]["date_from"] is None
    assert [h["post_id"] for h in state["relevant"]] == [1, 2, 4]


async def test_research_mode_verifies_externally_before_synthesis() -> None:
    llm = ScriptedLLM(
        route=Route(mode="research", topic=None),
        phrasings=["дедлайн"],
        keywords=["дедлайн", "Паркинсон"],
    )
    calls: list[dict[str, Any]] = []
    state = await run_agent(_deps(llm, _tools(calls=calls)), "правда ли, что дедлайны горят?")
    steps = [s["step"] for s in state["steps"]]
    assert steps[-2:] == ["verify_external", "synthesize"]
    web = [c for c in calls if c["tool"] != "search_index"]
    assert [c["tool"] for c in web] == ["web_search", "fetch_url"]
    # Wikipedia is searched by the extracted names, not by the conversational request
    assert web[0]["args"]["query"] == "дедлайн Паркинсон"
    assert (
        state["verification"][0]["title"] == "Дедлайн" and "[source: Википедия]" in state["answer"]
    )


async def test_research_lookup_falls_back_to_the_request_without_keywords() -> None:
    llm = ScriptedLLM(route=Route(mode="research", topic=None), phrasings=["дедлайн"])
    calls: list[dict[str, Any]] = []
    await run_agent(_deps(llm, _tools(calls=calls)), "правда ли, что дедлайны горят?")
    web = [c for c in calls if c["tool"] == "web_search"]
    assert web and web[0]["args"]["query"] == "правда ли, что дедлайны горят?"


async def test_budget_exhaustion_yields_a_caveat() -> None:
    llm = ScriptedLLM(phrasings=["понедельник"], relevant_word="нет")
    state = await run_agent(_deps(llm, _tools(), max_iterations=1), "мемы")
    assert state["caveat"] and "бюджет шагов" in state["caveat"]
    assert [s["step"] for s in state["steps"]].count("retrieve") == 1


async def test_failures_degrade_instead_of_crashing() -> None:
    llm = ScriptedLLM(fail={"Route", "Grades", "complete"}, phrasings=["дедлайн"])
    state = await run_agent(_deps(llm, _tools(search_fails=True)), "дедлайн")
    assert state["mode"] == "search"  # router failure → default mode
    assert any(d.startswith("route:") for d in state["degraded"])
    assert any(d == "search_index:unavailable" for d in state["degraded"])
    assert state["answer"] and "Не удалось" in state["answer"]
    assert any(d.startswith("synthesize:") for d in state["degraded"])
