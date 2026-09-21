from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.collector.conftest import T0
from tests.retrieval.conftest import FakeEmbedder
from tgdigest.agent.toolset import ToolDeps, build_registry
from tgdigest.db.base import Base, make_engine, make_session_factory
from tgdigest.db.models import Channel, Cluster, Enrichment, Post, PostCluster
from tgdigest.ingest.entities import Cache, Grounder
from tgdigest.retrieval.index import PostIndex
from tgdigest.retrieval.runner import build_index


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    f = make_session_factory(engine)
    async with f() as s:
        s.add(Channel(id=1, username="memes", topic="humor", title="Мемы"))
        s.add(Channel(id=2, username="sf", topic="scifi", title="Фантастика"))
        s.add_all(
            [
                Post(
                    id=10,
                    channel_id=1,
                    tg_message_id=100,
                    posted_at=T0,
                    text="кот и дедлайн",
                    media_type="photo",
                    media_path="a.jpg",
                    views=500,
                ),
                Post(id=12, channel_id=2, tg_message_id=300, posted_at=T0, text="Лем и Стругацкие"),
                Post(
                    id=13,
                    channel_id=2,
                    tg_message_id=301,
                    posted_at=T0,
                    text="Лем и Стругацкие (репост)",
                ),
            ]
        )
        s.add(
            Enrichment(
                post_id=10,
                ocr_text="ПОНЕДЕЛЬНИК",
                vlm_caption="кот",
                topic_labels=["humor"],
                is_ad=False,
                quality_score=0.8,
                model_version="v1",
            )
        )
        s.add(Cluster(id=1, topic="scifi", representative_post_id=12, first_seen_at=T0, size=2))
        s.add_all(
            [
                PostCluster(post_id=12, cluster_id=1, stage="text"),
                PostCluster(post_id=13, cluster_id=1, stage="text"),
            ]
        )
        await s.commit()
    return f


def _http(routes: dict[str, Any]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                if isinstance(body, dict):
                    return httpx.Response(200, json=body)
                return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
async def registry(factory: async_sessionmaker[AsyncSession]) -> Any:
    index = PostIndex(QdrantClient(":memory:"), FakeEmbedder())
    await build_index(factory, index)
    wiki = {
        "query": {"search": [{"title": "Станислав Лем", "snippet": "польский <b>писатель</b>"}]}
    }
    article = (
        "<html><head><title>Статья</title></head><body><article>"
        + "Текст статьи о Леме. " * 20
        + "</article></body></html>"
    )
    http = _http(
        {
            "https://ru.wikipedia.org": wiki,
            "https://example.com/article": article,
            "https://example.com/down": httpx.ConnectError("boom"),
        }
    )
    grounder = Grounder(client=http, cache=Cache())
    confirmed: list[str] = []

    async def confirmer(tool: str, args: dict[str, Any]) -> bool:
        confirmed.append(tool)
        return args.get("user_id") == 7

    reg = build_registry(
        ToolDeps(index=index, factory=factory, http=http, grounder=grounder), confirmer=confirmer
    )
    return reg


async def test_search_index_uses_product_filters_and_returns_rows(registry: Any) -> None:
    r = await registry.call("search_index", {"query": "Лем", "limit": 5})
    ids = [h["post_id"] for h in r.data["hits"]]
    assert (
        r.ok and ids[0] == 12 and 13 not in ids
    )  # the duplicate is collapsed to its representative
    r = await registry.call("search_index", {"query": "кот", "channels": ["@memes"]})
    assert r.ok and r.data["hits"][0]["post_id"] == 10 and r.data["hits"][0]["ocr"] == "ПОНЕДЕЛЬНИК"
    r = await registry.call("search_index", {"query": "кот", "channels": ["nobody"]})
    assert r.error_kind == "not_found"
    assert (
        await registry.call("search_index", {"query": "", "limit": 99})
    ).error_kind == "invalid_args"


async def test_get_post_and_list_channels(registry: Any) -> None:
    r = await registry.call("get_post", {"post_id": 12})
    assert r.ok and r.data["url"] == "https://t.me/sf/300"
    assert r.data["cluster"]["size"] == 2 and [d["post_id"] for d in r.data["duplicates"]] == [13]
    r = await registry.call("get_post", {"post_id": 10})
    assert (
        r.data["ocr"] == ["ПОНЕДЕЛЬНИК"] and r.data["label"] == "humor" and r.data["views"] == 500
    )
    assert (await registry.call("get_post", {"post_id": 999})).error_kind == "not_found"
    r = await registry.call("list_channels", {"topic": "scifi"})
    assert r.ok and r.data == [
        {"username": "sf", "title": "Фантастика", "topic": "scifi", "subscribers": None, "posts": 2}
    ]


async def test_external_tools_report_their_source_and_failures(registry: Any) -> None:
    r = await registry.call("web_search", {"query": "Лем"})
    assert r.ok and r.data["source"] == "ru.wikipedia.org"
    assert r.data["results"][0]["snippet"] == "польский писатель"
    r = await registry.call("fetch_url", {"url": "https://example.com/article"})
    assert r.ok and r.data["title"] == "Статья" and "Леме" in r.data["text"]
    r = await registry.call("fetch_url", {"url": "https://example.com/down"})
    assert r.error_kind == "unavailable" and "unreachable" in (r.error or "")
    r = await registry.call("lookup_film", {"title": "Несуществующий фильм", "year": 2026})
    assert r.error_kind == "not_found"  # Wikidata route is not mocked: 404 → unresolved


async def test_profile_is_read_freely_but_written_only_with_confirmation(registry: Any) -> None:
    r = await registry.call("get_profile", {"user_id": 7})
    assert r.ok and r.data["new"] is True
    denied = await registry.call("update_profile", {"user_id": 1, "topic_weights": {"humor": 0.9}})
    assert denied.error_kind == "needs_confirmation"
    ok = await registry.call(
        "update_profile",
        {"user_id": 7, "topic_weights": {"humor": 0.9}, "negative_prefs": ["реклама"]},
    )
    assert (
        ok.ok
        and ok.data["topic_weights"] == {"humor": 0.9}
        and ok.data["negative_prefs"] == ["реклама"]
    )
    again = await registry.call(
        "update_profile", {"user_id": 7, "negative_prefs": ["реклама", "спойлеры"]}
    )
    assert again.data["negative_prefs"] == ["реклама", "спойлеры"]
    bad = await registry.call("update_profile", {"user_id": 7, "topic_weights": {"humor": 5}})
    assert bad.error_kind == "invalid_args"
    r = await registry.call("get_profile", {"user_id": 7})
    assert r.data["topic_weights"] == {"humor": 0.9} and "new" not in r.data
    schemas = {t["function"]["name"] for t in registry.openai_tools()}
    assert schemas == {
        "search_index",
        "get_post",
        "list_channels",
        "web_search",
        "fetch_url",
        "lookup_film",
        "get_profile",
        "update_profile",
    }
    json.dumps(registry.openai_tools())  # serialisable for the chat API
