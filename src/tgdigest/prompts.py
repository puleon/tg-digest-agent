"""Versioned prompt files under ``prompts/`` (SPEC §11.5: no prompts hardcoded in code)."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@cache
def load_prompt(name: str, version: int, *, root: Path = PROMPTS_DIR) -> str:
    return (root / f"{name}.v{version}.md").read_text(encoding="utf-8").strip()


@cache
def load_examples(
    name: str, version: int, *, root: Path = PROMPTS_DIR
) -> tuple[dict[str, Any], ...]:
    with (root / f"{name}.examples.v{version}.yaml").open(encoding="utf-8") as fh:
        return tuple(yaml.safe_load(fh) or [])


def prompt_id(name: str, version: int) -> str:
    return f"{name}.v{version}"
