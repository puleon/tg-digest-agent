from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from tgdigest.agent.tools import Tool, ToolError, ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(min_length=1)
    times: int = Field(default=1, ge=1, le=3)


async def echo(args: BaseModel) -> Any:
    a = EchoArgs.model_validate(args.model_dump())
    return a.text * a.times


async def slow(args: BaseModel) -> Any:
    await asyncio.sleep(1)
    return "late"


async def missing(args: BaseModel) -> Any:
    raise ToolError("not_found", "no such post")


async def buggy(args: BaseModel) -> Any:
    raise KeyError("oops")


async def write(args: BaseModel) -> Any:
    return "written"


def _registry(confirmer: Any = None) -> ToolRegistry:
    return ToolRegistry(
        [
            Tool("echo", "repeat", EchoArgs, echo),
            Tool("slow", "sleeps", EchoArgs, slow, timeout_s=0.05),
            Tool("missing", "404", EchoArgs, missing),
            Tool("buggy", "raises", EchoArgs, buggy),
            Tool("write", "writes", EchoArgs, write, writes=True),
        ],
        confirmer=confirmer,
    )


async def test_valid_call_returns_data_and_timing() -> None:
    r = await _registry().call("echo", {"text": "ab", "times": 2})
    assert r.ok and r.data == "abab" and r.seconds >= 0 and r.args == {"text": "ab", "times": 2}


async def test_every_failure_is_a_result_with_a_kind() -> None:
    reg = _registry()
    assert (await reg.call("nope", {})).error_kind == "not_found"
    bad = await reg.call("echo", {"text": "", "times": 9})
    assert bad.error_kind == "invalid_args" and not bad.ok
    assert (await reg.call("slow", {"text": "x"})).error_kind == "timeout"
    assert (await reg.call("missing", {"text": "x"})).error_kind == "not_found"
    crashed = await reg.call("buggy", {"text": "x"})
    assert crashed.error_kind == "failed" and "KeyError" in (crashed.error or "")
    assert "failed (timeout)" in (await reg.call("slow", {"text": "x"})).summary()


async def test_writes_need_a_confirmation() -> None:
    assert (await _registry().call("write", {"text": "x"})).error_kind == "needs_confirmation"

    async def deny(tool: str, args: dict[str, Any]) -> bool:
        return False

    async def allow(tool: str, args: dict[str, Any]) -> bool:
        return tool == "write"

    assert (await _registry(deny).call("write", {"text": "x"})).error_kind == "needs_confirmation"
    assert (await _registry(allow).call("write", {"text": "x"})).data == "written"


def test_openai_schemas_carry_the_argument_constraints() -> None:
    schemas = _registry().openai_tools(["echo"])
    assert len(schemas) == 1 and schemas[0]["function"]["name"] == "echo"
    params = schemas[0]["function"]["parameters"]
    assert params["properties"]["times"]["maximum"] == 3 and params["required"] == ["text"]
