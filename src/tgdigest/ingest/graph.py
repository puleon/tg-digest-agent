"""The ingest agent as a LangGraph state machine: triage → (vision) → classify → injection.

The unit of work is a *post*: one Telegram message, or an album whose members share one
caption (SPEC §6.1). Every image member gets its own caption/OCR; the classifier and the
injection detector see the caption plus every member's OCR once. Every step degrades
explicitly instead of failing the post: a broken image, a model timeout or unparseable JSON
leaves a note in ``degraded`` and the pipeline continues with what it has.
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
from tgdigest.ingest.vision_cache import VisionCache
from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, Usage, image_part
from tgdigest.prompts import load_examples, load_prompt, prompt_id

log = structlog.get_logger(__name__)

VISUAL_TOPICS = frozenset({"humor", "cinema"})  # SPEC §6.2: the VLM branch is mandatory here
IMAGE_TYPES = frozenset({"photo", "image", "video", "animation"})
MAX_IMAGES_PER_POST = 6  # the classifier sees at most this many members' OCR/captions
CLASSIFY_PROMPT = ("classify_post", 1)
VISION_PROMPT = ("vlm_describe", 1)
VISION_SIMPLE_PROMPT = ("vlm_describe_simple", 1)
_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


class Member(TypedDict):
    """One Telegram message of a post; an album has several, a plain post exactly one."""

    post_id: int
    media_type: str | None
    media_path: str | None


class IngestState(TypedDict, total=False):
    # input — a post: one message, or an album whose members share the caption
    post_id: int
    """Id of the first message (the one that carries the caption)."""
    members: list[Member]
    channel_topic: str
    text: str
    is_forward: bool
    # triage
    clean_text: str
    urls: list[str]
    needs_vision: bool
    # results
    vision: dict[int, dict[str, Any] | None]
    """Per member post_id; missing key = no image, None = vision failed."""
    labels: dict[str, Any] | None
    injection_flag: bool
    injection_hits: list[str]
    degraded: list[str]
    usage: dict[str, int]


@dataclass
class IngestDeps:
    llm: LLMClient
    media_root: Path
    vision_timeout_s: float = 300.0
    """Per attempt; with 6 posts in flight and 4 VLM slots a request can queue for a while."""
    read_image: Callable[[Path], Awaitable[bytes]] | None = None
    vision_cache: VisionCache | None = None
    """Captions/OCR read earlier (the refresh's vision pass); cache-only means a miss is a
    failed read rather than a call to the vision tier."""

    async def image_bytes(self, rel: str) -> bytes:
        path = self.media_root / rel
        if self.read_image is not None:
            return await self.read_image(path)
        return await asyncio.to_thread(path.read_bytes)


def model_version(deps: IngestDeps) -> str:
    models = f"{deps.llm.model_for('fast')}+{deps.llm.model_for('vlm')}"
    return f"{models}|{prompt_id(*CLASSIFY_PROMPT)}|{prompt_id(*VISION_PROMPT)}"


def _add_usage(current: dict[str, int], usage: Usage) -> dict[str, int]:
    return {
        "prompt_tokens": current["prompt_tokens"] + usage.prompt_tokens,
        "completion_tokens": current["completion_tokens"] + usage.completion_tokens,
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


def image_members(state: IngestState) -> list[Member]:
    return [
        m
        for m in state.get("members", [])
        if m["media_path"] is not None and (m["media_type"] or "") in IMAGE_TYPES
    ]


def needs_vision(state: IngestState) -> bool:
    """Images are read for the visual topics, and elsewhere when the text alone is too short."""
    clean = normalize_text(state.get("text", ""))
    return bool(image_members(state)) and (
        state.get("channel_topic") in VISUAL_TOPICS or len(clean) < 40
    )


async def _describe(
    deps: IngestDeps, prompt: str, image: bytes, mime: str, max_tokens: int
) -> tuple[VisionResult, Usage]:
    content: list[Any] = [{"type": "text", "text": prompt}, image_part(image, mime)]
    messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": content}]
    result, completion = await asyncio.wait_for(
        deps.llm.structured(messages, VisionResult, tier="vlm", max_tokens=max_tokens),
        timeout=deps.vision_timeout_s,
    )
    return result, completion.usage


async def describe_member(
    deps: IngestDeps, member: Member
) -> tuple[dict[str, Any] | None, dict[str, int], list[str]]:
    """One image through the vision tier: (result or None, usage, degradation notes)."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    rel = member["media_path"] or ""
    pid = member["post_id"]
    try:
        image = await deps.image_bytes(rel)
    except OSError as exc:
        log.warning("image_unreadable", post_id=pid, error=repr(exc))
        return None, usage, ["vision:missing_file"]
    mime = _MIME.get(Path(rel).suffix.lower(), "image/jpeg")
    # 300 tokens fit ~95 % of answers; a truncated one is retried with 600 by the client (D4)
    attempts = [(load_prompt(*VISION_PROMPT), 300), (load_prompt(*VISION_SIMPLE_PROMPT), 200)]
    for prompt, max_tokens in attempts:  # SPEC §6.2: retry with a simpler prompt, then degrade
        try:
            result, used = await _describe(deps, prompt, image, mime, max_tokens)
        except (LLMOutputError, LLMError, TimeoutError) as exc:
            log.warning("vision_failed", post_id=pid, error=repr(exc)[:200])
            continue
        usage = _add_usage(usage, used)
        notes = [] if result.is_readable or result.ocr_text else ["vision:unreadable"]
        return result.model_dump(), usage, notes
    return None, usage, ["vision:failed"]


def build_ingest_graph(deps: IngestDeps) -> Any:
    async def triage(state: IngestState) -> IngestState:
        return {
            "clean_text": normalize_text(state.get("text", "")),
            "urls": extract_urls(state.get("text", "")),
            "needs_vision": needs_vision(state),
            "vision": {},
            "labels": None,
            "degraded": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    async def describe_images(state: IngestState) -> IngestState:
        usage = state["usage"]
        degraded = list(state["degraded"])
        vision: dict[int, dict[str, Any] | None] = {}
        cache = deps.vision_cache
        for member in image_members(state):  # sequential per post; posts run concurrently
            pid = member["post_id"]
            if cache is not None and pid in cache:
                vision[pid], notes = cache.get(pid)
            elif cache is not None and cache.only:
                vision[pid], notes = None, ["vision:not_cached"]
            else:
                vision[pid], used, notes = await describe_member(deps, member)
                usage = {k: usage[k] + used[k] for k in usage}
            degraded.extend(notes)
        return {"vision": vision, "usage": usage, "degraded": degraded}

    async def classify(state: IngestState) -> IngestState:
        parts = [state["clean_text"]]
        described = [v for v in (state.get("vision") or {}).values() if v]
        for v in described[:MAX_IMAGES_PER_POST]:
            if v.get("ocr_text"):
                parts.append(f"[text in image: {v['ocr_text']}]")
            if v.get("caption"):
                parts.append(f"[image: {v['caption']}]")
        if not described:
            kinds = sorted({m["media_type"] for m in state.get("members", []) if m["media_type"]})
            if kinds:
                parts.append(f"[media: {', '.join(kinds)}]")
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
        return {
            "labels": labels.model_dump(),
            "usage": _add_usage(state["usage"], completion.usage),
        }

    async def detect_injection(state: IngestState) -> IngestState:
        ocr = [v.get("ocr_text") or "" for v in (state.get("vision") or {}).values() if v]
        flag, hits = injection_score("\n".join([state["clean_text"], *ocr]))
        return {"injection_flag": flag, "injection_hits": hits}

    def route_after_triage(state: IngestState) -> Literal["describe_images", "classify"]:
        return "describe_images" if state["needs_vision"] else "classify"

    graph: StateGraph[IngestState] = StateGraph(IngestState)
    graph.add_node("triage", triage)
    graph.add_node("describe_images", describe_images)
    graph.add_node("classify", classify)
    graph.add_node("detect_injection", detect_injection)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_after_triage)
    graph.add_edge("describe_images", "classify")
    graph.add_edge("classify", "detect_injection")
    graph.add_edge("detect_injection", END)
    return graph.compile()
