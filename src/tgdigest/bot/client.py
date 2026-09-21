"""The bot's view of the API: a thin async HTTP client, so the bot can run anywhere that reaches
Telegram (the box cannot) while the models stay in one API process."""

from __future__ import annotations

from typing import Any

import httpx


class ApiClient:
    def __init__(self, base_url: str, *, timeout_s: float = 1800.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self._http.request(method, path, **kw)
        resp.raise_for_status()
        return resp.json()

    async def search(self, query: str, user_id: int) -> dict[str, Any]:
        return dict(await self._json("POST", "/search", json={"query": query, "user_id": user_id}))

    async def digest(self, user_id: int, *, minutes: int = 10, days: int = 7) -> dict[str, Any]:
        return dict(
            await self._json(
                "POST", "/digest", json={"user_id": user_id, "minutes": minutes, "days": days}
            )
        )

    async def latest_digest(self, user_id: int) -> dict[str, Any] | None:
        resp = await self._http.get("/digest/latest", params={"user_id": user_id})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return dict(resp.json())

    async def why(self, digest_id: int, post_id: int) -> dict[str, Any] | None:
        resp = await self._http.get(f"/why/{digest_id}/{post_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return dict(resp.json())

    async def feedback(
        self,
        user_id: int,
        post_id: int,
        signal: str,
        *,
        context: str,
        digest_id: int | None = None,
        trace_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            await self._json(
                "POST",
                "/feedback",
                json={
                    "user_id": user_id,
                    "post_id": post_id,
                    "signal": signal,
                    "context": context,
                    "digest_id": digest_id,
                    "trace_id": trace_id,
                    "query": query,
                },
            )
        )

    async def profile(self, user_id: int) -> dict[str, Any]:
        return dict(await self._json("GET", f"/profile/{user_id}"))

    async def rebuild_profile(self, user_id: int) -> dict[str, Any]:
        return dict(await self._json("POST", f"/profile/{user_id}/rebuild"))

    async def schedule(self, user_id: int, hour: int | None) -> dict[str, Any]:
        return dict(await self._json("POST", f"/profile/{user_id}/schedule", json={"hour": hour}))

    async def scheduled_users(self) -> list[dict[str, Any]]:
        return list(await self._json("GET", "/users/scheduled"))

    async def health(self) -> dict[str, Any]:
        resp = await self._http.get("/health")
        return dict(resp.json())
