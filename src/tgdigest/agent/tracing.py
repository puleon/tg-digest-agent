"""Tracing of agent runs (SPEC §7): a root observation per run, one child per step, usage and
latency on each. Langfuse when keys are configured, a no-op otherwise — the graph never knows.

Usage is reported as Langfuse ``usage_details`` (input/output tokens). Cost in money is not
reported by the tracer: the models are local; ``scripts``/README convert tokens and seconds
to a public-price equivalent when a number is needed.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from tgdigest.config import Settings

log = structlog.get_logger(__name__)

Kind = str  # "agent" | "generation" | "tool" | "retriever" | "chain" | "span"


class StepHandle(Protocol):
    def end(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        model: str | None = None,
        level: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class RunHandle(Protocol):
    trace_id: str | None

    def step(self, name: str, *, kind: Kind = "span", input: Any = None) -> StepHandle: ...
    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None: ...


class Tracer(Protocol):
    def start_run(
        self,
        name: str,
        *,
        input: Any,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunHandle: ...
    def score(
        self, trace_id: str, name: str, value: float, *, comment: str | None = None
    ) -> bool: ...
    def flush(self) -> None: ...


# --- no-op ---------------------------------------------------------------------------------------
@dataclass
class _NoopStep:
    def end(self, **kw: Any) -> None:
        return None


@dataclass
class _NoopRun:
    trace_id: str | None = None

    def step(self, name: str, *, kind: Kind = "span", input: Any = None) -> StepHandle:
        return _NoopStep()

    def end(self, **kw: Any) -> None:
        return None


class NoopTracer:
    def start_run(self, name: str, **kw: Any) -> RunHandle:
        return _NoopRun()

    def score(self, trace_id: str, name: str, value: float, *, comment: str | None = None) -> bool:
        return False

    def flush(self) -> None:
        return None


# --- Langfuse ------------------------------------------------------------------------------------
@dataclass
class _LangfuseStep:
    obs: Any

    def end(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        model: str | None = None,
        level: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if output is not None:
            fields["output"] = output
        if metadata:
            fields["metadata"] = metadata
        if usage:
            fields["usage_details"] = {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
            }
        if model:
            fields["model"] = model
        if level:
            fields["level"] = level
        if status:
            fields["status_message"] = status
        try:
            if fields:
                self.obs.update(**fields)
            self.obs.end()
        except Exception as exc:  # tracing must never break a run
            log.warning("trace_step_failed", error=repr(exc)[:120])


@dataclass
class _LangfuseRun:
    obs: Any
    trace_id: str | None = None
    ctx: Any = None
    steps: list[_LangfuseStep] = field(default_factory=list)

    def step(self, name: str, *, kind: Kind = "span", input: Any = None) -> StepHandle:
        try:
            child = self.obs.start_observation(name=name, as_type=kind, input=input)
        except Exception as exc:
            log.warning("trace_step_failed", error=repr(exc)[:120])
            return _NoopStep()
        step = _LangfuseStep(child)
        self.steps.append(step)
        return step

    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None:
        try:
            self.obs.update(output=output, metadata=metadata)
            self.obs.set_trace_io(output=output)
            self.obs.end()
            if self.ctx is not None:
                self.ctx.__exit__(None, None, None)
        except Exception as exc:
            log.warning("trace_end_failed", error=repr(exc)[:120])


class LangfuseTracer:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> LangfuseTracer | None:
        if not settings.langfuse_public_key or not settings.langfuse_secret_key.get_secret_value():
            return None
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_host,
        )
        return cls(client)

    def start_run(
        self,
        name: str,
        *,
        input: Any,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunHandle:
        try:
            from langfuse import propagate_attributes

            ctx = propagate_attributes(user_id=user_id, trace_name=name) if user_id else None
            if ctx is not None:
                ctx.__enter__()
            obs = self.client.start_observation(
                name=name, as_type="agent", input=input, metadata=metadata
            )
            obs.set_trace_io(input=input)
            return _LangfuseRun(obs, trace_id=getattr(obs, "trace_id", None), ctx=ctx)
        except Exception as exc:
            log.warning("trace_start_failed", error=repr(exc)[:120])
            return _NoopRun()

    def score(self, trace_id: str, name: str, value: float, *, comment: str | None = None) -> bool:
        """Attach a score to a trace (SPEC §7.4: user feedback and judge verdicts → traces)."""
        try:
            self.client.create_score(
                trace_id=trace_id, name=name, value=value, data_type="NUMERIC", comment=comment
            )
            return True
        except Exception as exc:
            log.warning("trace_score_failed", trace_id=trace_id, error=repr(exc)[:120])
            return False

    def flush(self) -> None:
        try:
            self.client.flush()
        except Exception as exc:
            log.warning("trace_flush_failed", error=repr(exc)[:120])


def make_tracer(settings: Settings) -> Tracer:
    return LangfuseTracer.from_settings(settings) or NoopTracer()


current_run: contextvars.ContextVar[RunHandle | None] = contextvars.ContextVar(
    "tgdigest_current_run", default=None
)
