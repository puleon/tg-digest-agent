"""Thin, provider-agnostic LLM client over an OpenAI-compatible endpoint (SPEC §2.8).

Three tiers share one endpoint (llama-server router mode): ``fast`` for mass operations,
``vlm`` for images, ``heavy`` for synthesis and judging. Structured output goes through
``response_format=json_schema`` (grammar-constrained on llama-server) and is validated with
pydantic; an invalid answer gets exactly one repair attempt before ``LLMOutputError`` — the
caller decides how to degrade (SPEC §6.2).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from tgdigest.config import Settings

log = structlog.get_logger(__name__)

Tier = Literal["fast", "heavy", "vlm"]
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Transport or server failure after the SDK's own retries."""


class LLMOutputError(LLMError):
    """The model answered, but not with the JSON we asked for (after one repair attempt)."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


@dataclass
class Completion:
    text: str
    model: str
    usage: Usage
    finish_reason: str | None = None
    latency_ms: float | None = None
    """Server-side generation time when the backend reports it (llama-server ``timings``)."""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def image_part(data: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.removesuffix("```").strip()
    return t


class LLMClient:
    def __init__(self, settings: Settings, *, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_s,
            max_retries=3,  # covers the router's keep-alive drops (D1 report, finding 6)
        )
        self._models: dict[Tier, str] = {
            "fast": settings.llm_model_fast,
            "heavy": settings.llm_model_heavy,
            "vlm": settings.vlm_model,
        }

    def model_for(self, tier: Tier) -> str:
        return self._models[tier]

    async def complete(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        tier: Tier = "fast",
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        thinking: bool = False,
    ) -> Completion:
        model = self.model_for(tier)
        extra: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": thinking}}
        budget = max_tokens
        if tier == "heavy":
            # gpt-oss has no "none" level and reasons before every answer; the reasoning
            # tokens are billed against max_tokens, so give it a low effort and room for it
            extra["reasoning_effort"] = "high" if thinking else "low"
            budget = max_tokens + self._settings.llm_heavy_reasoning_tokens
        elif not thinking:
            extra["reasoning_effort"] = "none"
        kwargs: dict[str, Any] = {}
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=list(messages),
                max_tokens=budget,
                temperature=temperature,
                extra_body=extra,
                **kwargs,
            )
        except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
            raise LLMError(f"{model}: {exc!r}") from exc
        choice = resp.choices[0]
        usage = Usage(
            getattr(resp.usage, "prompt_tokens", 0) or 0,
            getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        raw = resp.model_dump()
        timings = raw.get("timings") or {}
        latency = (timings.get("prompt_ms") or 0) + (timings.get("predicted_ms") or 0) or None
        return Completion(
            text=choice.message.content or "",
            model=resp.model or model,
            usage=usage,
            finish_reason=choice.finish_reason,
            latency_ms=latency,
            raw=raw,
        )

    async def structured(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        schema: type[T],
        *,
        tier: Tier = "fast",
        max_tokens: int = 512,
        temperature: float = 0.0,
        thinking: bool = False,
    ) -> tuple[T, Completion]:
        """Ask for ``schema`` as JSON; validate; repair once; raise ``LLMOutputError``."""
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        completion = await self.complete(
            messages,
            tier=tier,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            thinking=thinking,
        )
        try:
            return schema.model_validate_json(_strip_fences(completion.text)), completion
        except ValidationError as exc:
            log.warning(
                "llm_invalid_json",
                model=completion.model,
                finish=completion.finish_reason,
                error=str(exc)[:200],
            )
            first_error = exc

        if completion.finish_reason == "length":
            # Truncated mid-JSON: asking for a fix cannot help, more room can (once).
            retry = await self.complete(
                messages,
                tier=tier,
                max_tokens=max_tokens * 2,
                temperature=temperature,
                response_format=response_format,
                thinking=thinking,
            )
            retry.usage = completion.usage + retry.usage
            try:
                return schema.model_validate_json(_strip_fences(retry.text)), retry
            except ValidationError as exc:
                raise LLMOutputError(
                    f"invalid JSON after a larger budget: {exc.errors()[0]['msg']}", retry.text
                ) from exc

        repair: list[ChatCompletionMessageParam] = [
            *messages,
            {"role": "assistant", "content": completion.text},
            {
                "role": "user",
                "content": (
                    "Your previous answer was not valid for the required JSON schema: "
                    f"{first_error.errors()[0].get('msg', 'invalid')}. "
                    "Reply with the corrected JSON only."
                ),
            },
        ]
        retry = await self.complete(
            repair,
            tier=tier,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            thinking=thinking,
        )
        retry.usage = completion.usage + retry.usage
        try:
            return schema.model_validate_json(_strip_fences(retry.text)), retry
        except ValidationError as exc:
            raise LLMOutputError(
                f"invalid JSON after repair: {exc.errors()[0]}", retry.text
            ) from exc


def dumps_schema_example(schema: type[BaseModel]) -> str:
    """Compact JSON schema string for prompts that want to show the expected shape."""
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
