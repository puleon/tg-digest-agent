from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx  # the openai SDK (v3) speaks httpx2, a fork of httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from tgdigest.config import Settings
from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, Usage, image_part


class Label(BaseModel):
    topic: str
    is_ad: bool


def _server(responses: list[Any]) -> tuple[AsyncOpenAI, list[dict[str, Any]]]:
    """An OpenAI client whose transport replays canned chat completions and records requests."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        item = responses.pop(0)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "nope"}})
        finish = "stop"
        if isinstance(item, tuple):
            item, finish = item
        body = {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": item},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "timings": {"prompt_ms": 100.0, "predicted_ms": 50.0},
        }
        return httpx.Response(200, json=body)

    client = AsyncOpenAI(
        base_url="http://llm/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client, seen


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, llm_model_fast="fast-m", llm_model_heavy="heavy-m")


async def test_complete_returns_text_usage_and_timings(settings: Settings) -> None:
    oai, seen = _server(["hello"])
    c = LLMClient(settings, client=oai)
    out = await c.complete([{"role": "user", "content": "hi"}], tier="heavy", max_tokens=7)
    assert out.text == "hello" and out.usage == Usage(10, 5) and out.latency_ms == 150.0
    assert seen[0]["model"] == "heavy-m" and seen[0]["max_tokens"] == 7
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[0]["reasoning_effort"] == "none"


async def test_structured_parses_and_strips_code_fences(settings: Settings) -> None:
    oai, seen = _server(['```json\n{"topic": "humor", "is_ad": false}\n```'])
    parsed, _ = await LLMClient(settings, client=oai).structured(
        [{"role": "user", "content": "classify"}], Label
    )
    assert parsed == Label(topic="humor", is_ad=False)
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["response_format"]["json_schema"]["name"] == "Label"


async def test_structured_repairs_once_and_sums_usage(settings: Settings) -> None:
    oai, seen = _server(['{"topic": "humor"}', '{"topic": "humor", "is_ad": true}'])
    parsed, comp = await LLMClient(settings, client=oai).structured(
        [{"role": "user", "content": "classify"}], Label
    )
    assert parsed.is_ad is True and comp.usage == Usage(20, 10)
    assert len(seen) == 2
    assert seen[1]["messages"][-1]["role"] == "user"
    assert "not valid" in seen[1]["messages"][-1]["content"]


async def test_structured_gives_up_after_one_repair(settings: Settings) -> None:
    oai, _ = _server(["garbage", "still garbage"])
    with pytest.raises(LLMOutputError) as exc:
        await LLMClient(settings, client=oai).structured([{"role": "user", "content": "x"}], Label)
    assert exc.value.raw == "still garbage"


async def test_transport_errors_become_llm_error(settings: Settings) -> None:
    oai, _ = _server([503])
    with pytest.raises(LLMError):
        await LLMClient(settings, client=oai).complete([{"role": "user", "content": "x"}])


def test_image_part_is_a_data_url() -> None:
    part = image_part(b"\x89PNG", "image/png")
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_structured_retries_truncation_with_a_larger_budget(settings: Settings) -> None:
    oai, seen = _server([('{"topic": "hum', "length"), '{"topic": "humor", "is_ad": false}'])
    parsed, comp = await LLMClient(settings, client=oai).structured(
        [{"role": "user", "content": "classify"}], Label, max_tokens=50
    )
    assert parsed.topic == "humor" and comp.usage == Usage(20, 10)
    assert [m["max_tokens"] for m in seen] == [50, 100]
    assert len(seen[1]["messages"]) == 1  # a plain retry, not a repair conversation
