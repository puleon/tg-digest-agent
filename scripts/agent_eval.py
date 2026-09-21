"""D9/D10 — agent evaluation: router accuracy and degradation under tool failures.

route   run the LLM router over docs/experiments/d9-agent/routes.yaml; accuracy of mode,
        topic, period and spoiler flag; confusion matrix of modes; seconds per request
chaos   inject failures (search timeout / unavailable / empty, web tools down, step budget),
        20 runs per scenario, and grade each run: correct degradation = a meaningful answer or
        an honest limitation, no invented evidence, no crash (SPEC §8.5)

uv run python scripts/agent_eval.py route
uv run python scripts/agent_eval.py chaos --runs 20 --out data/eval/chaos
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from tgdigest.agent.graph import ROUTE_PROMPT, Route
from tgdigest.agent.tools import Tool, ToolError, ToolRegistry
from tgdigest.config import get_settings
from tgdigest.llm.client import LLMClient, LLMOutputError
from tgdigest.prompts import load_prompt

ROUTES = Path("docs/experiments/d9-agent/routes.yaml")


async def cmd_route(concurrency: int) -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    cases = list(yaml.safe_load(ROUTES.read_text(encoding="utf-8")))
    sem = asyncio.Semaphore(concurrency)
    system = load_prompt(*ROUTE_PROMPT)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            t0 = time.perf_counter()
            try:
                r, comp = await llm.structured(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": case["query"]},
                    ],
                    Route,
                    tier="fast",
                    max_tokens=150,
                )
                got: dict[str, Any] = r.model_dump()
                usage = comp.usage.total
            except LLMOutputError as exc:
                got = {
                    "mode": None,
                    "topic": None,
                    "days": None,
                    "exclude_spoilers": None,
                    "error": str(exc)[:60],
                }
                usage = 0
            return {"case": case, "got": got, "seconds": time.perf_counter() - t0, "tokens": usage}

    results = await asyncio.gather(*(one(c) for c in cases))
    ok: Counter[str] = Counter()
    conf: dict[tuple[str, str], int] = defaultdict(int)
    for r in results:
        c, g = r["case"], r["got"]
        ok["mode"] += g["mode"] == c["mode"]
        ok["topic"] += g["topic"] == c.get("topic")
        ok["period"] += bool(g.get("days")) == bool(c.get("period"))
        ok["spoilers"] += bool(g.get("exclude_spoilers")) == bool(c.get("exclude_spoilers"))
        conf[(c["mode"], str(g["mode"]))] += 1
        flag = "" if g["mode"] == c["mode"] else "  <-- expected " + c["mode"]
        print(
            f"{c['mode']:<8} {g['mode']!s:<8} {g['topic']!s:<7} days={g.get('days')!s:<4} "
            f"{c['query'][:60]}{flag}"
        )
    n = len(results)
    print(
        f"\n{n} requests · mode accuracy {ok['mode'] / n:.3f} · topic {ok['topic'] / n:.3f} · "
        f"period {ok['period'] / n:.3f} · spoiler flag {ok['spoilers'] / n:.3f} · "
        f"{sum(r['seconds'] for r in results) / n:.1f} s/request (concurrency {concurrency}) · "
        f"{sum(r['tokens'] for r in results) / n:.0f} tokens/request"
    )
    modes = ["search", "news", "research"]
    print(
        "\nconfusion (rows expected, cols got):\n        "
        + "".join(f"{m:>9}" for m in modes)
        + "    other"
    )
    for e in modes:
        other = sum(v for (a, b), v in conf.items() if a == e and b not in modes)
        print(f"{e:>8}" + "".join(f"{conf[(e, g)]:>9}" for g in modes) + f"{other:>9}")


class _Args(BaseModel):
    query: str = ""
    limit: int = 20
    topic: str | None = None
    date_from: str | None = None
    exclude_spoilers: bool = False
    lang: str = "ru"
    url: str = ""


SCENARIOS = ("search_timeout", "search_down", "search_empty", "web_down", "budget")
"""SPEC §8.5: search timeout, unavailable / empty search, 500 from the web tools, step budget.
The VLM invalid-JSON case is exercised by the ingest tests (vision degrades per member)."""


def _chaos_registry(real: ToolRegistry, scenario: str) -> ToolRegistry:
    """Wrap the real tools so one of them misbehaves on purpose."""

    async def search_timeout(args: BaseModel) -> Any:
        await asyncio.sleep(999)

    async def search_down(args: BaseModel) -> Any:
        raise ToolError("unavailable", "qdrant: connection refused")

    async def search_empty(args: BaseModel) -> Any:
        return {"query": "", "hits": [], "total": 0}

    async def web_down(args: BaseModel) -> Any:
        raise ToolError("unavailable", "HTTP 500")

    tools: list[Tool] = []
    for name in real.names():
        t = real.get(name)
        if scenario == "search_timeout" and name == "search_index":
            tools.append(Tool(name, t.description, _Args, search_timeout, timeout_s=2))
        elif scenario == "search_down" and name == "search_index":
            tools.append(Tool(name, t.description, _Args, search_down))
        elif scenario == "search_empty" and name == "search_index":
            tools.append(Tool(name, t.description, _Args, search_empty))
        elif scenario == "web_down" and name in ("web_search", "fetch_url"):
            tools.append(Tool(name, t.description, _Args, web_down))
        else:
            tools.append(t)
    return ToolRegistry(tools, confirmer=real.confirmer)


REQUESTS = [
    "мемы про дедлайн",
    "что нового по Нолану",
    "правда ли, что «Мумию» Кронина разгромили критики?",
    "трейлер «Одиссеи» Нолана",
    "органоиды мозга",
    "что-нибудь смешное про котов",
    "правда ли, что Artemis II облетела Луну?",
    "новости фантастики за неделю",
    "постеры «Закулисья реальности»",
    "кто сыграет нового Джеймса Бонда",
]


def graded(scenario: str, state: dict[str, Any]) -> tuple[bool, str]:
    """Correct degradation = the run ended with a meaningful answer or an honest limitation,
    and did not invent evidence it could not have had."""
    answer = state.get("answer") or ""
    caveat = bool(state.get("caveat"))
    citations = state.get("citations") or []
    degraded = state.get("degraded") or []
    if not answer:
        return False, "no answer"
    if scenario in ("search_timeout", "search_down", "search_empty"):
        if citations:
            return False, "cited posts without any retrieval"
        if not caveat and "не" not in answer.lower():
            return False, "no limitation stated"
        return True, "caveat, no citations"
    if scenario == "web_down":
        if "[source:" in answer:
            return False, "cited an external source that failed"
        if not any(d.startswith(("web_search", "fetch_url")) for d in degraded):
            return True, "web tools not needed (non-research route)"
        return True, "answered from posts, web failure recorded"
    if scenario == "budget":
        return (caveat, "caveat present" if caveat else "budget exhausted silently")
    return False, "unknown scenario"


async def cmd_chaos(runs: int, only: str | None, out: Path) -> None:
    from tgdigest.agent.cli import build_deps

    settings = get_settings()
    scenarios = [s for s in SCENARIOS if not only or s == only]
    reqs = (REQUESTS * ((runs + len(REQUESTS) - 1) // len(REQUESTS)))[:runs]
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "chaos.jsonl"
    done: set[tuple[str, int]] = set()
    if results_path.exists():
        for line in results_path.open(encoding="utf-8"):
            r = json.loads(line)
            done.add((r["scenario"], r["run"]))
    tally: dict[str, list[bool]] = defaultdict(list)
    async with build_deps(settings, rerank=False) as (tools, make_agent):
        with results_path.open("a", encoding="utf-8") as fh:
            for scenario in scenarios:
                chaos = tools if scenario == "budget" else _chaos_registry(tools, scenario)
                for i, q in enumerate(reqs):
                    if (scenario, i) in done:
                        continue
                    t0 = time.perf_counter()
                    try:
                        if scenario == "budget":
                            agent = make_agent(chaos)
                            agent_state = await agent(q, 0, max_iterations=1)
                        else:
                            agent_state = await make_agent(chaos)(q)
                        ok, why = graded(scenario, agent_state)
                        crashed = False
                    except Exception as exc:  # a crash is the failure we measure
                        agent_state, ok, why, crashed = {}, False, f"crash: {exc!r}"[:120], True
                    row = {
                        "scenario": scenario,
                        "run": i,
                        "query": q,
                        "ok": ok,
                        "why": why,
                        "crashed": crashed,
                        "caveat": bool(agent_state.get("caveat")),
                        "citations": len(agent_state.get("citations") or []),
                        "degraded": agent_state.get("degraded"),
                        "seconds": round(time.perf_counter() - t0, 1),
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    tally[scenario].append(ok)
                    print(
                        f"{scenario:<15} run {i:2d} ok={ok!s:<5} {why:<45} {row['seconds']:4.0f}s"
                    )
    _chaos_report(results_path)


def _chaos_report(results_path: Path) -> None:
    rows = [json.loads(line) for line in results_path.open(encoding="utf-8")]
    print("\n| scenario | runs | correct degradation | crashes | mean s |\n|---|---|---|---|---|")
    for s in SCENARIOS:
        rs = [r for r in rows if r["scenario"] == s]
        if not rs:
            continue
        ok = sum(r["ok"] for r in rs)
        print(
            f"| {s} | {len(rs)} | {ok / len(rs):.2f} ({ok}/{len(rs)}) | "
            f"{sum(r['crashed'] for r in rs)} | {sum(r['seconds'] for r in rs) / len(rs):.0f} |"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route")
    r.add_argument("--concurrency", type=int, default=2)
    c = sub.add_parser("chaos")
    c.add_argument("--runs", type=int, default=20, help="runs per scenario (SPEC §8.5: 20)")
    c.add_argument("--only", default=None, choices=list(SCENARIOS))
    c.add_argument("--out", type=Path, default=Path("data/eval/chaos"))
    c.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.cmd == "route":
        asyncio.run(cmd_route(args.concurrency))
    elif args.report:
        _chaos_report(args.out / "chaos.jsonl")
    else:
        asyncio.run(cmd_chaos(args.runs, args.only, args.out))


if __name__ == "__main__":
    main()
