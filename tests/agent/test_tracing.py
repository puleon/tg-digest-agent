from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.agent.test_graph import ScriptedLLM, _deps, _tools
from tgdigest.agent.graph import run_agent
from tgdigest.agent.tracing import NoopTracer, current_run


@dataclass
class RecordingStep:
    name: str
    kind: str
    input: Any
    ended: dict[str, Any] | None = None

    def end(self, **kw: Any) -> None:
        self.ended = kw


@dataclass
class RecordingRun:
    name: str
    input: Any
    user_id: str | None
    trace_id: str | None = "trace-1"
    steps: list[RecordingStep] = field(default_factory=list)
    ended: dict[str, Any] | None = None

    def step(self, name: str, *, kind: str = "span", input: Any = None) -> RecordingStep:
        s = RecordingStep(name, kind, input)
        self.steps.append(s)
        return s

    def end(self, **kw: Any) -> None:
        self.ended = kw


@dataclass
class RecordingTracer:
    runs: list[RecordingRun] = field(default_factory=list)
    flushed: int = 0

    def start_run(
        self, name: str, *, input: Any, user_id: str | None = None, metadata: Any = None
    ) -> RecordingRun:
        r = RecordingRun(name, input, user_id)
        self.runs.append(r)
        return r

    def flush(self) -> None:
        self.flushed += 1


async def test_every_step_becomes_an_observation_with_usage_and_output() -> None:
    tracer = RecordingTracer()
    llm = ScriptedLLM(phrasings=["мем про дедлайн"])
    state = await run_agent(_deps(llm, _tools(), tracer=tracer), "мем про дедлайн", user_id=7)
    run = tracer.runs[0]
    assert run.name == "agent" and run.input == "мем про дедлайн" and run.user_id == "7"
    assert [s.name for s in run.steps] == [s["step"] for s in state["steps"]]
    kinds = {s.name: s.kind for s in run.steps}
    assert kinds["route"] == "generation" and kinds["retrieve"] == "retriever"
    route = run.steps[0]
    assert route.ended and route.ended["usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert route.ended["model"] == "fast-model" and route.ended["output"]["mode"] == "search"
    assert run.ended and run.ended["output"] == state["answer"]
    assert (
        run.ended["metadata"]["tokens"]
        == state["usage"]["prompt_tokens"] + state["usage"]["completion_tokens"]
    )
    assert state["trace_id"] == "trace-1" and current_run.get() is None


async def test_noop_tracer_and_no_tracer_leave_the_state_untouched() -> None:
    llm = ScriptedLLM(phrasings=["мем про дедлайн"])
    traced = await run_agent(_deps(llm, _tools(), tracer=NoopTracer()), "мем про дедлайн")
    plain = await run_agent(
        _deps(ScriptedLLM(phrasings=["мем про дедлайн"]), _tools()), "мем про дедлайн"
    )
    assert (
        traced["answer"] == plain["answer"]
        and "trace_id" not in plain
        and traced["trace_id"] is None
    )
