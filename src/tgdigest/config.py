"""Application settings.

Everything is read from environment variables (or a local ``.env`` file); see ``.env.example``
for the documented list. Secrets never live in code — SPEC §2.6.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `TELEGRAM_API_ID=` in .env means "unset", not int("")
        extra="ignore",
        frozen=True,
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # --- storage ---------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://tgd:tgd@127.0.0.1:5432/tgd"
    qdrant_url: str = "http://127.0.0.1:6333"
    data_dir: Path = Path("data")

    # --- local model serving (OpenAI-compatible API, SPEC §2.8) ----------------------------
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_api_key: SecretStr = SecretStr("local")
    llm_model_fast: str = "qwen3.6-35b-a3b"
    """Mass operations: classification, scoring, query rewriting (SPEC §4)."""
    llm_model_heavy: str = "gpt-oss-120b"
    """Synthesis and judge; loaded on demand (SPEC §4, memory layout)."""
    vlm_model: str = "gemma-4-26b-a4b"
    """Caption + OCR for images (SPEC §6.2)."""
    llm_timeout_s: float = 600.0

    # --- observability ---------------------------------------------------------------------
    langfuse_host: str = "http://127.0.0.1:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")

    # --- telegram ---------------------------------------------------------------------------
    collector_source: Literal["web", "telethon"] = "web"
    """``web`` = Telegram's public preview (no account); ``telethon`` = MTProto user session."""
    web_delay_s: float = Field(default=0.8, ge=0)
    """Pause between web-preview requests — one channel at a time, politely."""
    web_proxy: str | None = None
    """Proxy for t.me / CDN traffic, e.g. ``socks5h://127.0.0.1:1080`` when the box itself
    cannot reach Telegram and a reverse SSH tunnel (`make collect`) provides the egress."""
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_session: str = "data/collector.session"
    bot_token: SecretStr | None = None

    # --- external tools ---------------------------------------------------------------------
    tmdb_api_key: SecretStr | None = None

    # --- agent budgets (SPEC §2.7, §6.5) ----------------------------------------------------
    agent_max_steps: int = Field(default=6, ge=1)
    agent_max_tokens: int = Field(default=30_000, ge=1)

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
