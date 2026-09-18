"""Structured outputs of the ingest agent (validated JSON from the models)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TopicLabel = Literal["scifi", "humor", "cinema", "other"]


class PostLabels(BaseModel):
    topic: TopicLabel
    is_ad: bool
    is_spoiler: bool
    quality: int = Field(ge=1, le=5)


class VisionResult(BaseModel):
    # ocr_text first: the grammar emits fields in schema order, so a truncated answer still
    # carries the part that matters most for retrieval
    ocr_text: str = ""
    caption: str = Field(default="", max_length=400)
    has_text: bool = False
    is_readable: bool = True
