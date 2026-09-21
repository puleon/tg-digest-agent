"""D9/D10 — agent evaluation: router accuracy and degradation under tool failures.

route   run the LLM router over docs/experiments/d9-agent/routes.yaml; accuracy of mode,
        topic, period and spoiler flag; confusion matrix of modes; seconds per request
chaos   run the agent on a few requests with tools that fail in controlled ways (timeouts,
        unavailability, bad JSON) and report that every run still ends with an answer and
        a recorded degradation (SPEC §8: graceful degradation)

uv run python scripts/agent_eval.py route
uv run python scripts/agent_eval.py chaos --requests 3
"""

from __future__ import annotations

import argparse
import asyncio
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


def _chaos_registry(real: ToolRegistry, scenario: str) -> ToolRegistry:
    """Wrap the real tools so one of them misbehaves on purpose."""

    async def search_timeout(args: BaseModel) -> Any:
        await asyncio.sleep(999)

    async def search_down(args: BaseModel) -> Any:
        raise ToolError("unavailable", "qdrant: connection refused")

    async def web_down(args: BaseModel) -> Any:
        raise ToolError("unavailable", "wikipedia: connection reset")

    tools: list[Tool] = []
    for name in real.names():
        t = real.get(name)
        if scenario == "search_timeout" and name == "search_index":
            tools.append(Tool(name, t.description, _Args, search_timeout, timeout_s=2))
        elif scenario == "search_down" and name == "search_index":
            tools.append(Tool(name, t.description, _Args, search_down))
        elif scenario == "web_down" and name in ("web_search", "fetch_url"):
            tools.append(Tool(name, t.description, _Args, web_down))
        else:
            tools.append(t)
    return ToolRegistry(tools, confirmer=real.confirmer)


async def cmd_chaos(requests: int) -> None:
    from tgdigest.agent.cli import build_deps

    settings = get_settings()
    scenarios = ["search_timeout", "search_down", "web_down"]
    reqs = [
        "мемы про дедлайн",
        "правда ли, что «Мумию» Кронина разгромили критики?",
        "что нового по Нолану",
    ][:requests]
    async with build_deps(settings, rerank=False) as (tools, make_agent):
        for scenario in scenarios:
            chaos = _chaos_registry(tools, scenario)
            for q in reqs:
                t0 = time.perf_counter()
                state = await make_agent(chaos)(q)
                print(
                    f"{scenario:<15} {q[:38]:<40} answered={bool(state.get('answer'))} "
                    f"caveat={bool(state.get('caveat'))} degraded={state.get('degraded')} "
                    f"steps={len(state.get('steps', []))} {time.perf_counter() - t0:.0f}s"
                )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route")
    r.add_argument("--concurrency", type=int, default=2)
    c = sub.add_parser("chaos")
    c.add_argument("--requests", type=int, default=3)
    args = ap.parse_args()
    if args.cmd == "route":
        asyncio.run(cmd_route(args.concurrency))
    else:
        asyncio.run(cmd_chaos(args.requests))


if __name__ == "__main__":
    main()
