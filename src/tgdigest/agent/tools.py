"""Tool contract for the agent (SPEC §6.5): every tool has a strict argument schema, a bounded
runtime and an explicit error — a failing tool returns a ``ToolResult`` with ``error.kind``,
it never raises into the graph. Writing tools run only after the user confirmed.

Error kinds: ``invalid_args`` (schema violation), ``not_found``, ``unavailable`` (network,
service down), ``timeout``, ``needs_confirmation`` (a write without confirmation),
``failed`` (anything else, with the exception name).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_S = 30.0


class ToolError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class ToolResult:
    name: str
    ok: bool
    data: Any = None
    error_kind: str | None = None
    error: str | None = None
    seconds: float = 0.0
    args: dict[str, Any] = field(default_factory=dict)

    def summary(self, limit: int = 300) -> str:
        if not self.ok:
            return f"{self.name} failed ({self.error_kind}): {self.error}"
        text = str(self.data)
        return text if len(text) <= limit else text[:limit] + "…"


class Confirmer(Protocol):
    async def __call__(self, tool: str, args: dict[str, Any]) -> bool: ...


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[BaseModel], Awaitable[Any]]
    writes: bool = False
    timeout_s: float = DEFAULT_TIMEOUT_S

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, *, confirmer: Confirmer | None = None):
        self._tools: dict[str, Tool] = {}
        self.confirmer = confirmer
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} registered twice")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError("not_found", f"unknown tool {name!r}") from None

    def openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values() if names is None or t.name in names]

    async def call(self, name: str, raw_args: dict[str, Any] | None = None) -> ToolResult:
        """Validate, confirm writes, run with a timeout; every failure becomes a result."""
        raw_args = raw_args or {}
        t0 = time.perf_counter()

        def fail(kind: str, message: str) -> ToolResult:
            log.warning("tool_failed", tool=name, kind=kind, error=message[:200])
            return ToolResult(
                name,
                False,
                error_kind=kind,
                error=message,
                seconds=round(time.perf_counter() - t0, 3),
                args=raw_args,
            )

        try:
            tool = self.get(name)
        except ToolError as exc:
            return fail(exc.kind, exc.message)
        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError as exc:
            return fail("invalid_args", str(exc.errors()[0].get("msg", exc))[:200])
        if tool.writes and (self.confirmer is None or not await self.confirmer(name, raw_args)):
            return fail("needs_confirmation", f"{name} changes the profile; confirm first")
        try:
            data = await asyncio.wait_for(tool.fn(args), timeout=tool.timeout_s)
        except TimeoutError:
            return fail("timeout", f"{name} exceeded {tool.timeout_s:.0f}s")
        except ToolError as exc:
            return fail(exc.kind, exc.message)
        except Exception as exc:  # a tool bug must not crash the agent: report and move on
            return fail("failed", f"{type(exc).__name__}: {exc}"[:200])
        seconds = round(time.perf_counter() - t0, 3)
        log.info("tool_ok", tool=name, seconds=seconds)
        return ToolResult(name, True, data=data, seconds=seconds, args=raw_args)
