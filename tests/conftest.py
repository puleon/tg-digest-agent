from __future__ import annotations

import pytest

from tgdigest.config import Settings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings isolated from the developer's .env and environment."""
    for var in ("DATABASE_URL", "QDRANT_URL", "LLM_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None)
