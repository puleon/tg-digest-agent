"""``config/channels.yaml`` — the corpus definition (SPEC §2.1, §9 D2)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Topic = Literal["scifi", "humor", "cinema"]


class ChannelSpec(BaseModel):
    username: str
    source: str = "manual"

    @field_validator("username")
    @classmethod
    def _strip_at(cls, v: str) -> str:
        v = v.strip().removeprefix("@").removeprefix("https://t.me/").removeprefix("t.me/")
        if not v:
            raise ValueError("empty channel username")
        return v


class TopicSpec(BaseModel):
    title: str = ""
    channels: list[ChannelSpec] = Field(default_factory=list)


class ChannelsConfig(BaseModel):
    topics: dict[Topic, TopicSpec]

    def flat(self) -> list[tuple[Topic, ChannelSpec]]:
        return [(topic, ch) for topic, spec in self.topics.items() for ch in spec.channels]


def load_channels_config(path: Path) -> ChannelsConfig:
    with path.open(encoding="utf-8") as fh:
        return ChannelsConfig.model_validate(yaml.safe_load(fh) or {})
