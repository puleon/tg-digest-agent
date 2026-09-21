"""One process, one set of heavy resources: models, index, DB, tracer, tools, agent.

``Services`` is what the API, the bot-facing endpoints and the CLIs share. ``open_services``
builds the real thing from settings; tests construct ``Services`` directly with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tgdigest.agent.graph import AgentDeps, AgentState, run_agent
from tgdigest.agent.tools import ToolRegistry
from tgdigest.agent.tracing import Tracer
from tgdigest.config import Settings
from tgdigest.db.models import Digest, Feedback, UserProfile
from tgdigest.digest.crew import DigestResult
from tgdigest.digest.runner import make_digest
from tgdigest.llm.client import LLMClient
from tgdigest.profile.model import Profile
from tgdigest.profile.runner import SIGNALS, build_and_store_profile, load_profile
from tgdigest.retrieval.index import PostIndex

log = structlog.get_logger(__name__)

FEEDBACK_SCORE = {"like": 1.0, "save": 1.0, "dislike": -1.0, "skip": 0.0}


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    index: PostIndex
    llm: LLMClient
    tracer: Tracer
    http: httpx.AsyncClient
    tools: ToolRegistry
    agent: AgentDeps
    digest_tier: str = "fast"

    # --- search --------------------------------------------------------------------------------
    async def ask(self, query: str, *, user_id: int = 0) -> AgentState:
        state = await run_agent(self.agent, query, user_id=user_id)
        self.tracer.flush()
        return state

    # --- digest --------------------------------------------------------------------------------
    async def digest(
        self, user_id: int, *, minutes: int = 10, days: int = 7, store: bool = True
    ) -> tuple[DigestResult, int | None]:
        result, digest_id = await make_digest(
            self.factory,
            self.index,
            self.llm,
            user_id,
            minutes=minutes,
            window_days=days,
            tier=self.digest_tier,
            store=store,
            tracer=self.tracer,
        )
        self.tracer.flush()
        return result, digest_id

    async def get_digest(self, digest_id: int) -> Digest | None:
        async with self.factory() as session:
            return await session.get(Digest, digest_id)

    async def latest_digest(self, user_id: int) -> Digest | None:
        async with self.factory() as session:
            stmt = (
                select(Digest)
                .where(Digest.user_id == user_id)
                .order_by(Digest.created_at.desc(), Digest.id.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    # --- feedback ------------------------------------------------------------------------------
    async def feedback(
        self,
        user_id: int,
        post_id: int,
        signal: str,
        *,
        context: str = "search",
        query: str | None = None,
        digest_id: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, bool]:
        """Store the reaction (SPEC §6.8) and score the trace it came from, when known."""
        if signal not in SIGNALS:
            raise ValueError(f"signal must be one of {SIGNALS}")
        async with self.factory() as session:
            if digest_id is not None and trace_id is None:
                digest = await session.get(Digest, digest_id)
                trace_id = digest.trace_id if digest else None
            row = Feedback(
                user_id=user_id,
                post_id=post_id,
                signal=signal,
                context=context,
                query=query,
                digest_id=digest_id,
            )
            session.add(row)
            await session.commit()
            feedback_id = int(row.id)
        scored = False
        if trace_id:
            scored = self.tracer.score(
                trace_id,
                "user_feedback",
                FEEDBACK_SCORE[signal],
                comment=f"{signal} on post {post_id} ({context})",
            )
            self.tracer.flush()
        log.info("feedback", user_id=user_id, post_id=post_id, signal=signal, scored=scored)
        return feedback_id, scored

    # --- profile -------------------------------------------------------------------------------
    async def profile(self, user_id: int) -> Profile | None:
        async with self.factory() as session:
            return await load_profile(session, user_id)

    async def rebuild_profile(self, user_id: int) -> Profile:
        return await build_and_store_profile(self.factory, self.index, user_id)

    async def set_schedule(self, user_id: int, hour: int | None) -> dict[str, Any]:
        """Daily digest hour (UTC) or None to unsubscribe; kept in the profile's stats."""
        async with self.factory() as session:
            row = await session.get(UserProfile, user_id)
            if row is None:
                row = UserProfile(user_id=user_id)
                session.add(row)
            stats = dict(row.stats_json or {})
            if hour is None:
                stats.pop("digest_hour", None)
            else:
                stats["digest_hour"] = int(hour)
            row.stats_json = stats
            await session.commit()
            return stats

    async def scheduled_users(self) -> list[dict[str, Any]]:
        async with self.factory() as session:
            rows = (await session.execute(select(UserProfile))).scalars().all()
        return [
            {"user_id": r.user_id, "digest_hour": int((r.stats_json or {})["digest_hour"])}
            for r in rows
            if (r.stats_json or {}).get("digest_hour") is not None
        ]

    async def close(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


async def open_services(
    settings: Settings,
    *,
    rerank: bool = True,
    threads: int | None = None,
    tier: str = "fast",
    rewrite: bool = True,
    confirmer: Any = None,
) -> Services:
    from qdrant_client import QdrantClient

    from tgdigest.agent.toolset import ToolDeps, build_registry, make_http
    from tgdigest.agent.tracing import make_tracer
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.ingest.entities import DbCache, Grounder
    from tgdigest.retrieval.embeddings import BGEM3Embedder
    from tgdigest.retrieval.rerank import BGEReranker

    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    http = make_http(proxy=settings.web_proxy)
    tracer = make_tracer(settings)
    llm = LLMClient(settings)
    index = PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=threads))
    deps = ToolDeps(
        index=index,
        factory=factory,
        http=http,
        grounder=Grounder(client=http, cache=DbCache(factory)),
        reranker=BGEReranker(threads=threads) if rerank else None,
    )
    tools = build_registry(deps, confirmer=confirmer)
    agent = AgentDeps(
        llm=llm,
        tools=tools,
        synthesis_tier=tier,  # type: ignore[arg-type]
        max_iterations=settings.agent_max_steps,
        max_tokens=settings.agent_max_tokens,
        rewrite=rewrite,
        tracer=tracer,
    )
    log.info("services_ready", rerank=rerank, tier=tier, at=datetime.now(UTC).isoformat())
    return Services(
        settings=settings,
        engine=engine,
        factory=factory,
        index=index,
        llm=llm,
        tracer=tracer,
        http=http,
        tools=tools,
        agent=agent,
        digest_tier=tier,
    )
