from __future__ import annotations

import pytest

from tgdigest.config import Settings


def test_defaults_point_to_localhost(settings: Settings) -> None:
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert "127.0.0.1" in settings.qdrant_url
    assert settings.llm_base_url.endswith("/v1")
    assert settings.langfuse_enabled is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_FAST", "some-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    s = Settings(_env_file=None)
    assert s.llm_model_fast == "some-model"
    assert s.agent_max_steps == 3
    assert s.langfuse_enabled is True


def test_secrets_do_not_leak_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    s = Settings(_env_file=None)
    assert "123:abc" not in repr(s)
    assert s.bot_token is not None
    assert s.bot_token.get_secret_value() == "123:abc"


def test_budgets_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MAX_STEPS", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)
