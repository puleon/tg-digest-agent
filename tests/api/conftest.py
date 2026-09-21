from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.agent.test_graph import ScriptedLLM
from tests.digest.test_crew import CrewLLM
from tests.retrieval.conftest import FakeEmbedder
from tgdigest.agent.graph import AgentDeps
from tgdigest.agent.toolset import ToolDeps, build_registry
from tgdigest.agent.tracing import NoopTracer
from tgdigest.api.app import create_app
from tgdigest.config import Settings
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.digest.crew import DigestDraft, Verdict
from tgdigest.ingest.entities import Cache, Grounder
from tgdigest.llm.client import Completion
from tgdigest.retrieval.index import PostIndex
from tgdigest.retrieval.runner import build_index
from tgdigest.service import Services

NOW = datetime.now(UTC)


class ComboLLM(ScriptedLLM):
    """Search steps from ScriptedLLM, digest steps from CrewLLM — one fake for the whole API."""

    def __init__(self) -> None:
        super().__init__(phrasings=["кот дедлайн"], relevant_word="кот")
        self.crew = CrewLLM()

    async def structured(
        self, messages: Any, schema: type[BaseModel], **kw: Any
    ) -> tuple[Any, Completion]:
        if schema in (DigestDraft, Verdict):
            return await self.crew.structured(messages, schema, **kw)
        return await super().structured(messages, schema, **kw)


@dataclass
class RecordingTracer(NoopTracer):
    scores: list[tuple[str, str, float]] = field(default_factory=list)

    def score(self, trace_id: str, name: str, value: float, *, comment: str | None = None) -> bool:
        self.scores.append((trace_id, name, value))
        return True


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def services(engine: AsyncEngine) -> AsyncIterator[Services]:
    factory = make_session_factory(engine)
    async with factory() as s:
        s.add_all(
            [
                Channel(id=1, username="memes", topic="humor", title="Мемы"),
                Channel(id=2, username="memes2", topic="humor"),
                Channel(id=3, username="sf", topic="scifi"),
                Channel(id=4, username="films", topic="cinema"),
            ]
        )
        posts = {
            1: ("кот и дедлайн", 1, 1),
            2: ("кот спит", 1, 2),
            3: ("кот у ноутбука", 2, 3),
            4: ("понедельник опять", 2, 1),
            5: ("Лем и Стругацкие", 3, 1),
            6: ("новая книга Дукая", 3, 5),
            7: ("трейлер фильма", 4, 2),
            8: ("рецензия", 4, 6),
        }
        for pid, (text, ch, age) in posts.items():
            s.add(
                Post(
                    id=pid,
                    channel_id=ch,
                    tg_message_id=100 + pid,
                    posted_at=NOW - timedelta(days=age),
                    text=text,
                    views=100 * pid,
                    media_type="photo",
                    media_path="a.jpg",
                )
            )
            s.add(
                Enrichment(
                    post_id=pid,
                    topic_labels=["humor"],
                    is_ad=False,
                    quality_score=4.0,
                    model_version="v1",
                )
            )
        await s.commit()
    index = PostIndex(QdrantClient(":memory:"), FakeEmbedder())
    await build_index(factory, index)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    llm: Any = ComboLLM()
    tracer = RecordingTracer()
    tools = build_registry(
        ToolDeps(
            index=index, factory=factory, http=http, grounder=Grounder(client=http, cache=Cache())
        )
    )
    agent = AgentDeps(llm=llm, tools=tools, tracer=tracer)
    svc = Services(
        settings=Settings(database_url="sqlite+aiosqlite://"),
        engine=engine,
        factory=factory,
        index=index,
        llm=llm,
        tracer=tracer,
        http=http,
        tools=tools,
        agent=agent,
    )
    yield svc
    await http.aclose()


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    with TestClient(create_app(services)) as c:
        yield c
