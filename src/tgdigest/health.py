"""Reachability checks for every backing service — used by ``tgdigest health`` and ``make up``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tgdigest.config import Settings


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


async def check_postgres(settings: Settings) -> Check:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
        return Check("postgres", True, str(version).split(" on ")[0])
    except Exception as exc:  # a health check reports, it does not raise
        return Check("postgres", False, repr(exc))
    finally:
        await engine.dispose()


_HTTP_TIMEOUT_S = 5.0


async def _http_check(name: str, url: str) -> Check:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(url)
        return Check(name, resp.status_code < 400, f"HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        return Check(name, False, repr(exc))


async def check_all(settings: Settings) -> list[Check]:
    llm_root = settings.llm_base_url.removesuffix("/v1")
    return list(
        await asyncio.gather(
            check_postgres(settings),
            _http_check("qdrant", f"{settings.qdrant_url}/healthz"),
            _http_check("llm", f"{llm_root}/health"),
            _http_check("langfuse", f"{settings.langfuse_host}/api/public/health"),
        )
    )
