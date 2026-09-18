from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from tgdigest.ingest.graph import IngestDeps
from tgdigest.ingest.schemas import PostLabels, VisionResult
from tgdigest.llm.client import Completion, LLMOutputError, Usage


@dataclass
class FakeLLM:
    """Stands in for LLMClient: canned answers per schema, records every call."""

    labels: PostLabels = field(
        default_factory=lambda: PostLabels(topic="humor", is_ad=False, is_spoiler=False, quality=3)
    )
    vision: VisionResult | None = field(
        default_factory=lambda: VisionResult(
            ocr_text="КОГДА ДЕДЛАЙН", caption="a meme", has_text=True
        )
    )
    vision_errors: int = 0  # raise LLMOutputError for this many vision calls first
    vision_delay_s: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    def model_for(self, tier: str) -> str:
        return {"fast": "fast-m", "vlm": "vlm-m", "heavy": "heavy-m"}[tier]

    async def structured(
        self, messages: Any, schema: type[BaseModel], *, tier: str = "fast", **kw: Any
    ) -> tuple[BaseModel, Completion]:
        self.calls.append({"tier": tier, "schema": schema.__name__, "messages": messages, **kw})
        comp = Completion(text="{}", model=self.model_for(tier), usage=Usage(10, 5))
        if schema is VisionResult:
            if self.vision_delay_s:
                import asyncio

                await asyncio.sleep(self.vision_delay_s)
            if self.vision_errors > 0:
                self.vision_errors -= 1
                raise LLMOutputError("bad json", "garbage")
            assert self.vision is not None
            return self.vision, comp
        return self.labels, comp


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    (tmp_path / "001").mkdir()
    (tmp_path / "001" / "1.jpg").write_bytes(b"\xff\xd8fake")
    return tmp_path


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def deps(fake_llm: FakeLLM, media_root: Path) -> IngestDeps:
    return IngestDeps(llm=fake_llm, media_root=media_root, vision_timeout_s=0.2)  # type: ignore[arg-type]
