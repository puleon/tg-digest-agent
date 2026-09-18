"""The ingest agent as a LangGraph state machine: triage → (vision) → classify → injection.

Every step degrades explicitly instead of failing the post: a broken image, a model timeout or
unparseable JSON leaves a note in ``degraded`` and the pipeline continues with what it has.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from openai.types.chat import ChatCompletionMessageParam

from tgdigest.ingest.injection import injection_score
from tgdigest.ingest.normalize import extract_urls, normalize_text
from tgdigest.ingest.schemas import PostLabels, VisionResult
from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, Usage, image_part
from tgdigest.prompts import load_examples, load_prompt, prompt_id

log = structlog.get_logger(__name__)

VISUAL_TOPICS = frozenset({"humor", "cinema"})  # SPEC §6.2: the VLM branch is mandatory here
CLASSIFY_PROMPT = ("classify_post", 1)
VISION_PROMPT = ("vlm_describe", 1)
VISION_SIMPLE_PROMPT = ("vlm_describe_simple", 1)
_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


class IngestState(TypedDict, total=False):
    # input
    post_id: int
    channel_topic: str
    text: str
    media_type: str | None
    media_path: str | None
    is_forward: bool
    # triage
    clean_text: str
    urls: list[str]
    needs_vision: bool
    # results
    vision: dict[str, Any] | None
    labels: dict[str, Any] | None
    injection_flag: bool
    injection_hits: list[str]
    degraded: list[str]
    usage: dict[str, int]


@dataclass
class IngestDeps:
    llm: LLMClient
    media_root: Path
    vision_timeout_s: float = 180.0
    read_image: Callable[[Path], Awaitable[bytes]] | None = None

    async def image_bytes(self, rel: str) -> bytes:
        path = self.media_root / rel
        if self.read_image is not None:
            return await self.read_image(path)
        return await asyncio.to_thread(path.read_bytes)


def model_version(deps: IngestDeps) -> str:
    models = f"{deps.llm.model_for('fast')}+{deps.llm.model_for('vlm')}"
    return f"{models}|{prompt_id(*CLASSIFY_PROMPT)}|{prompt_id(*VISION_PROMPT)}"


def _add_usage(state: IngestState, usage: Usage) -> dict[str, int]:
    cur = state.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": cur["prompt_tokens"] + usage.prompt_tokens,
        "completion_tokens": cur["completion_tokens"] + usage.completion_tokens,
    }


def _examples_block() -> str:
    lines = []
    for ex in load_examples(*CLASSIFY_PROMPT):
        media = f" [media: {ex['media']}]" if ex.get("media") else ""
        labels = ex["labels"]
        lines.append(
            f"<post>{ex['text'].strip()}{media}</post> → "
            f'{{"topic": "{labels["topic"]}", "is_ad": {str(labels["is_ad"]).lower()}, '
            f'"is_spoiler": {str(labels["is_spoiler"]).lower()}, "quality": {labels["quality"]}}}'
        )
    return "\n".join(lines)


def build_ingest_graph(deps: IngestDeps) -> Any:
    async def triage(state: IngestState) -> IngestState:
        clean = normalize_text(state.get("text", ""))
        has_image = state.get("media_path") is not None and (state.get("media_type") or "") in {
            "photo",
            "image",
            "video",
            "animation",
        }
        needs_vision = has_image and (
            state.get("channel_topic") in VISUAL_TOPICS or len(clean) < 40
        )
        return {
            "clean_text": clean,
            "urls": extract_urls(state.get("text", "")),
            "needs_vision": needs_vision,
            "vision": None,
            "labels": None,
            "degraded": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    async def _describe(
        prompt: str, image: bytes, mime: str, max_tokens: int
    ) -> tuple[VisionResult, Usage]:
        content: list[Any] = [{"type": "text", "text": prompt}, image_part(image, mime)]
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
        result, completion = await asyncio.wait_for(
            deps.llm.structured(messages, VisionResult, tier="vlm", max_tokens=max_tokens),
            timeout=deps.vision_timeout_s,
        )
        return result, completion.usage

    async def describe_image(state: IngestState) -> IngestState:
        rel = state["media_path"] or ""
        try:
            image = await deps.image_bytes(rel)
        except OSError as exc:
            log.warning("image_unreadable", post_id=state["post_id"], error=repr(exc))
            return {"vision": None, "degraded": [*state["degraded"], "vision:missing_file"]}
        mime = _MIME.get(Path(rel).suffix.lower(), "image/jpeg")
        attempts = [(load_prompt(*VISION_PROMPT), 400), (load_prompt(*VISION_SIMPLE_PROMPT), 200)]
        usage = state["usage"]
        for prompt, max_tokens in attempts:  # SPEC §6.2: retry with a simpler prompt, then degrade
            try:
                result, used = await _describe(prompt, image, mime, max_tokens)
            except (LLMOutputError, LLMError, TimeoutError) as exc:
                log.warning("vision_failed", post_id=state["post_id"], error=repr(exc)[:200])
                continue
            usage = _add_usage({"usage": usage}, used)
            if not result.is_readable and not result.ocr_text:
                return {
                    "vision": result.model_dump(),
                    "usage": usage,
                    "degraded": [*state["degraded"], "vision:unreadable"],
                }
            return {"vision": result.model_dump(), "usage": usage}
        return {"vision": None, "usage": usage, "degraded": [*state["degraded"], "vision:failed"]}

    async def classify(state: IngestState) -> IngestState:
        vision = state.get("vision") or {}
        parts = [state["clean_text"]]
        if vision.get("ocr_text"):
            parts.append(f"[text in image: {vision['ocr_text']}]")
        if vision.get("caption"):
            parts.append(f"[image: {vision['caption']}]")
        elif state.get("media_type"):
            parts.append(f"[media: {state['media_type']}]")
        if state.get("is_forward"):
            parts.append("[forwarded]")
        body = "\n".join(p for p in parts if p).strip() or "[empty post]"
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": load_prompt(*CLASSIFY_PROMPT).format(examples=_examples_block()),
            },
            {"role": "user", "content": f"<post>\n{body}\n</post>"},
        ]
        try:
            labels, completion = await deps.llm.structured(
                messages, PostLabels, tier="fast", max_tokens=60
            )
        except (LLMOutputError, LLMError) as exc:
            log.warning("classify_failed", post_id=state["post_id"], error=repr(exc)[:200])
            return {"labels": None, "degraded": [*state["degraded"], "classify:failed"]}
        return {"labels": labels.model_dump(), "usage": _add_usage(state, completion.usage)}

    async def detect_injection(state: IngestState) -> IngestState:
        vision = state.get("vision") or {}
        flag, hits = injection_score("\n".join([state["clean_text"], vision.get("ocr_text") or ""]))
        return {"injection_flag": flag, "injection_hits": hits}

    def route_after_triage(state: IngestState) -> Literal["describe_image", "classify"]:
        return "describe_image" if state["needs_vision"] else "classify"

    graph: StateGraph[IngestState] = StateGraph(IngestState)
    graph.add_node("triage", triage)
    graph.add_node("describe_image", describe_image)
    graph.add_node("classify", classify)
    graph.add_node("detect_injection", detect_injection)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_after_triage)
    graph.add_edge("describe_image", "classify")
    graph.add_edge("classify", "detect_injection")
    graph.add_edge("detect_injection", END)
    return graph.compile()
