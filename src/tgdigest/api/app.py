"""FastAPI application (SPEC §6.8): search, digest, feedback, profile, /why, /health, /metrics.

``create_app(services)`` takes an already-built :class:`Services` so tests can pass fakes;
``main()`` builds the real one from settings inside the lifespan.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from tgdigest import __version__
from tgdigest.api.metrics import metrics
from tgdigest.config import Settings, get_settings
from tgdigest.digest.runner import as_json as digest_json
from tgdigest.health import check_all
from tgdigest.service import Services, open_services

log = structlog.get_logger(__name__)
Signal = Literal["like", "dislike", "save", "skip"]


# --- schemas --------------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    user_id: int = Field(default=0, ge=0)


class PostRef(BaseModel):
    post_id: int
    channel: str | None = None
    date: str | None = None
    topic: str | None = None
    text: str = ""
    url: str | None = None


class SearchResponse(BaseModel):
    answer: str
    mode: str | None
    topic: str | None
    days: int | None
    citations: list[int]
    posts: list[PostRef]
    iterations: int
    usage: dict[str, int]
    degraded: list[str]
    caveat: str | None
    trace_id: str | None
    seconds: float


class DigestRequest(BaseModel):
    user_id: int = Field(default=0, ge=0)
    minutes: int = Field(default=10, ge=3, le=40)
    days: int = Field(default=7, ge=1, le=90)
    store: bool = True


class FeedbackRequest(BaseModel):
    user_id: int = Field(default=0, ge=0)
    post_id: int = Field(ge=1)
    signal: Signal
    context: Literal["search", "digest", "onboarding"] = "search"
    query: str | None = Field(default=None, max_length=500)
    digest_id: int | None = None
    trace_id: str | None = Field(default=None, max_length=64)


class ScheduleRequest(BaseModel):
    hour: int | None = Field(default=None, ge=0, le=23, description="UTC hour, null = off")


def _post_refs(state: dict[str, Any]) -> list[PostRef]:
    cited = set(state.get("citations") or [])
    rows = state.get("relevant") or state.get("graded") or state.get("hits") or []
    out = []
    for h in rows:
        if cited and int(h["post_id"]) not in cited:
            continue
        out.append(
            PostRef(
                post_id=int(h["post_id"]),
                channel=h.get("channel"),
                date=h.get("date"),
                topic=h.get("topic"),
                text=(h.get("text") or "")[:200],
                url=h.get("url") or (f"https://t.me/{h['channel']}" if h.get("channel") else None),
            )
        )
    return out


def explain(item: dict[str, Any], plan: dict[str, Any]) -> str:
    """Why a post made the issue — from its score components, in plain Russian, no LLM."""
    parts = item.get("score_parts") or {}
    reasons = []
    sim = parts.get("similarity")
    if sim is not None:
        if sim >= 0.7:
            reasons.append(f"очень похоже на то, что вам нравилось (близость {sim:.2f})")
        elif sim >= 0.45:
            reasons.append(f"похоже на ваши интересы (близость {sim:.2f})")
        else:
            reasons.append(f"по смыслу далеко от ваших лайков (близость {sim:.2f})")
    topic = parts.get("topic")
    if topic is not None:
        reasons.append(
            f"тема «{item.get('topic')}» "
            + ("в приоритете" if topic >= 0.6 else "не в приоритете")
            + f" в профиле ({topic:.2f})"
        )
    channel = parts.get("channel")
    if channel is not None:
        reasons.append(f"канал @{item.get('channel')}: доля лайков {channel:.2f}")
    engagement = parts.get("engagement")
    if engagement is not None:
        reasons.append(
            "пост заметно популярнее обычного для канала"
            if engagement >= 0.7
            else "популярность в норме для канала"
            if engagement >= 0.4
            else "пост тише обычного для канала"
        )
    slot = plan.get("per_topic", {}).get(item.get("topic"))
    if slot:
        reasons.append(f"в плане выпуска на тему {item.get('topic')} отведено {slot} мест")
    if item.get("fresh"):
        reasons.append("свежий (последние 3 дня)")
    total = item.get("score")
    head = f"Итоговый балл {total:.2f}. " if isinstance(total, int | float) else ""
    return head + "; ".join(reasons) + "." if reasons else head + "Компоненты балла не сохранены."


# --- app -------------------------------------------------------------------------------------
def create_app(services: Services | None = None, *, settings: Settings | None = None) -> FastAPI:
    holder: dict[str, Services] = {}
    if services is not None:
        holder["s"] = services

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = "s" not in holder
        if owned:
            holder["s"] = await open_services(settings or get_settings())
        try:
            yield
        finally:
            if owned:
                await holder["s"].close()

    app = FastAPI(title="Tematic Telegram Index", version=__version__, lifespan=lifespan)

    def svc() -> Services:
        return holder["s"]

    @app.middleware("http")
    async def measure(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        t0 = time.perf_counter()
        endpoint = request.url.path.split("/")[1] or "root"
        try:
            response = await call_next(request)
        except Exception:
            metrics.inc("tgdigest_requests_total", {"endpoint": endpoint, "status": "500"})
            raise
        metrics.inc(
            "tgdigest_requests_total", {"endpoint": endpoint, "status": str(response.status_code)}
        )
        metrics.observe(
            "tgdigest_request_seconds", time.perf_counter() - t0, {"endpoint": endpoint}
        )
        return response

    @app.get("/health")
    async def health() -> Response:
        checks = await check_all(svc().settings)
        body = {c.name: {"ok": c.ok, "detail": c.detail} for c in checks}
        status = 200 if all(c.ok for c in checks) else 503
        return Response(
            content=json.dumps({"status": "ok" if status == 200 else "degraded", "checks": body}),
            status_code=status,
            media_type="application/json",
        )

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    @app.post("/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        t0 = time.perf_counter()
        state = await svc().ask(req.query, user_id=req.user_id)
        usage = dict(state.get("usage") or {})
        metrics.inc(
            "tgdigest_llm_tokens_total",
            {"kind": "prompt", "endpoint": "search"},
            usage.get("prompt_tokens", 0),
        )
        metrics.inc(
            "tgdigest_llm_tokens_total",
            {"kind": "completion", "endpoint": "search"},
            usage.get("completion_tokens", 0),
        )
        metrics.observe("tgdigest_agent_iterations", float(state.get("iteration") or 0))
        return SearchResponse(
            answer=state.get("answer", ""),
            mode=state.get("mode"),
            topic=state.get("topic"),
            days=state.get("days"),
            citations=list(state.get("citations") or []),
            posts=_post_refs(dict(state)),
            iterations=int(state.get("iteration") or 0),
            usage=usage,
            degraded=list(state.get("degraded") or []),
            caveat=state.get("caveat"),
            trace_id=state.get("trace_id"),
            seconds=round(time.perf_counter() - t0, 2),
        )

    @app.post("/digest")
    async def digest(req: DigestRequest) -> dict[str, Any]:
        result, digest_id = await svc().digest(
            req.user_id, minutes=req.minutes, days=req.days, store=req.store
        )
        metrics.inc(
            "tgdigest_llm_tokens_total",
            {"kind": "prompt", "endpoint": "digest"},
            result.usage.prompt_tokens,
        )
        metrics.inc(
            "tgdigest_llm_tokens_total",
            {"kind": "completion", "endpoint": "digest"},
            result.usage.completion_tokens,
        )
        metrics.observe("tgdigest_critic_iterations", float(result.critic_iterations))
        return digest_json(result, digest_id)

    @app.get("/digest/latest")
    async def digest_latest(user_id: int = 0) -> dict[str, Any]:
        row = await svc().latest_digest(user_id)
        if row is None:
            raise HTTPException(404, "no digest yet")
        return _digest_row(row)

    @app.get("/digest/{digest_id}")
    async def digest_get(digest_id: int) -> dict[str, Any]:
        row = await svc().get_digest(digest_id)
        if row is None:
            raise HTTPException(404, f"no digest {digest_id}")
        return _digest_row(row)

    @app.get("/why/{digest_id}/{post_id}")
    async def why(digest_id: int, post_id: int) -> dict[str, Any]:
        row = await svc().get_digest(digest_id)
        if row is None:
            raise HTTPException(404, f"no digest {digest_id}")
        item = next((it for it in row.items_json if int(it.get("post_id", -1)) == post_id), None)
        if item is None:
            raise HTTPException(404, f"post {post_id} is not in digest {digest_id}")
        return {
            "digest_id": digest_id,
            "post_id": post_id,
            "title": item.get("title"),
            "explanation": explain(item, row.plan_json or {}),
            "score": item.get("score"),
            "score_parts": item.get("score_parts") or {},
            "plan": row.plan_json,
        }

    @app.post("/feedback")
    async def feedback(req: FeedbackRequest) -> dict[str, Any]:
        try:
            feedback_id, scored = await svc().feedback(
                req.user_id,
                req.post_id,
                req.signal,
                context=req.context,
                query=req.query,
                digest_id=req.digest_id,
                trace_id=req.trace_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        metrics.inc("tgdigest_feedback_total", {"signal": req.signal, "context": req.context})
        return {"id": feedback_id, "scored": scored}

    @app.get("/profile/{user_id}")
    async def profile(user_id: int) -> dict[str, Any]:
        p = await svc().profile(user_id)
        if p is None:
            return {"user_id": user_id, "new": True}
        return {"user_id": user_id, **p.to_json(), "interest_centroids": len(p.interest_centroids)}

    @app.post("/profile/{user_id}/rebuild")
    async def profile_rebuild(user_id: int) -> dict[str, Any]:
        p = await svc().rebuild_profile(user_id)
        return {"user_id": user_id, **p.to_json(), "interest_centroids": len(p.interest_centroids)}

    @app.post("/profile/{user_id}/schedule")
    async def schedule(user_id: int, req: ScheduleRequest) -> dict[str, Any]:
        stats = await svc().set_schedule(user_id, req.hour)
        return {"user_id": user_id, "digest_hour": stats.get("digest_hour")}

    @app.get("/users/scheduled")
    async def scheduled() -> list[dict[str, Any]]:
        return await svc().scheduled_users()

    @app.get("/post/{post_id}")
    async def post(post_id: int) -> dict[str, Any]:
        res = await svc().tools.call("get_post", {"post_id": post_id})
        if not res.ok:
            raise HTTPException(404 if res.error_kind == "not_found" else 502, res.error or "")
        return dict(res.data)

    return app


def _digest_row(row: Any) -> dict[str, Any]:
    return {
        "digest_id": row.id,
        "user_id": row.user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "plan": row.plan_json,
        "items": row.items_json,
        "critic_iterations": row.critic_iterations,
        "trace_id": row.trace_id,
    }


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings=settings), host=settings.api_host, port=settings.api_port)
