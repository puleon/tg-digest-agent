"""The agent's tools (SPEC §6.5), built over the index, Postgres and the external sources.

| tool             | source                                   | write |
|------------------|------------------------------------------|-------|
| search_index     | Qdrant hybrid search + filters           |       |
| get_post         | Postgres: post + enrichment + cluster    |       |
| list_channels    | Postgres                                 |       |
| web_search       | Wikipedia search API (ru, en)            |       |
| fetch_url        | HTTP GET + readable text (ingest/links)  |       |
| lookup_film      | Wikidata (ingest/entities)               |       |
| get_profile      | Postgres user_profile                    |       |
| update_profile   | Postgres user_profile                    |  ✓    |

``web_search`` is Wikipedia, not a general web engine: the box has no search-API key and
scraping a search engine is not a dependable tool contract. It is named for the SPEC's
interface; the report says what it really is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.agent.tools import Confirmer, Tool, ToolError, ToolRegistry
from tgdigest.db.models import Channel, Cluster, Enrichment, Post, PostCluster, UserProfile
from tgdigest.ingest.entities import FilmMention, Grounder
from tgdigest.ingest.links import fetch_page
from tgdigest.retrieval.index import PostIndex, SearchFilters
from tgdigest.retrieval.rerank import Reranker
from tgdigest.retrieval.search import SearchConfig, run_search

Topic = Literal["scifi", "humor", "cinema"]
WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"


# --- argument schemas --------------------------------------------------------------------------
class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="search query, post-like wording")
    topic: Topic | None = Field(default=None, description="restrict to one topic")
    channels: list[str] | None = Field(default=None, description="channel usernames", max_length=10)
    date_from: datetime | None = None
    date_to: datetime | None = None
    exclude_spoilers: bool = Field(default=False, description="drop posts labelled as spoilers")
    limit: int = Field(default=10, ge=1, le=30)


class PostArgs(BaseModel):
    post_id: int = Field(ge=1)


class ChannelsArgs(BaseModel):
    topic: Topic | None = None


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    lang: Literal["ru", "en"] = "ru"
    limit: int = Field(default=5, ge=1, le=10)


class FetchArgs(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


class FilmArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1880, le=2100)
    original_title: str | None = Field(default=None, max_length=200)


class ProfileArgs(BaseModel):
    user_id: int = Field(default=0, ge=0)


class ProfileChange(BaseModel):
    user_id: int = Field(default=0, ge=0)
    topic_weights: dict[str, float] | None = Field(
        default=None, description="topic -> weight in [0, 1]; merged into the profile"
    )
    negative_prefs: list[str] | None = Field(
        default=None, description="things to exclude (phrases, channels); appended", max_length=20
    )


# --- implementation -----------------------------------------------------------------------------
@dataclass
class ToolDeps:
    index: PostIndex
    factory: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    grounder: Grounder
    reranker: Reranker | None = None
    channel_ids: dict[str, int] | None = None
    """username -> id, resolved lazily from the channels table."""


def _hit_row(h: Any) -> dict[str, Any]:
    p = h.payload
    return {
        "post_id": h.post_id,
        "rank": h.rank,
        "score": round(h.score, 4),
        "channel": p.get("channel"),
        "topic": p.get("topic"),
        "date": p.get("date"),
        "label": p.get("label"),
        "is_ad": p.get("is_ad"),
        "is_spoiler": p.get("is_spoiler"),
        "has_media": p.get("has_media"),
        "text": (p.get("text") or "")[:300],
        "ocr": ((p.get("sources") or {}).get("ocr") or "")[:200],
        "caption": ((p.get("sources") or {}).get("caption") or "")[:200],
        "cluster_id": p.get("cluster_id"),
        "url": p.get("url"),
    }


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def build_registry(deps: ToolDeps, *, confirmer: Confirmer | None = None) -> ToolRegistry:
    async def channel_ids() -> dict[str, int]:
        if deps.channel_ids is None:
            async with deps.factory() as session:
                rows = (await session.execute(select(Channel.username, Channel.id))).all()
            deps.channel_ids = {u.lower(): int(i) for u, i in rows}
        return deps.channel_ids

    async def search_index(args: BaseModel) -> Any:
        a = SearchArgs.model_validate(args.model_dump())
        ids: list[int] | None = None
        if a.channels:
            known = await channel_ids()
            ids = [
                known[c.lower().lstrip("@")] for c in a.channels if c.lower().lstrip("@") in known
            ]
            if not ids:
                raise ToolError("not_found", f"none of the channels {a.channels} is in the corpus")
        filters = SearchFilters(
            topic=a.topic,
            channels=ids,
            date_from=_as_utc(a.date_from),
            date_to=_as_utc(a.date_to),
            exclude_ads=True,
            exclude_spoilers=a.exclude_spoilers,
            representatives_only=True,
        )
        cfg = SearchConfig(
            variant="full",
            mode="hybrid",
            limit=a.limit,
            rerank_depth=30 if deps.reranker else 0,
        )
        hits = await asyncio.to_thread(  # embedder + reranker are CPU-bound: keep the loop free
            run_search, deps.index, [a.query], config=cfg, filters=filters, reranker=deps.reranker
        )
        return {"query": a.query, "hits": [_hit_row(h) for h in hits], "total": len(hits)}

    async def get_post(args: BaseModel) -> Any:
        a = PostArgs.model_validate(args.model_dump())
        async with deps.factory() as session:
            row = (
                await session.execute(
                    select(Post, Channel.username, Channel.topic)
                    .join(Channel, Channel.id == Post.channel_id)
                    .where(Post.id == a.post_id)
                )
            ).first()
            if row is None:
                raise ToolError("not_found", f"post {a.post_id} does not exist")
            post, username, topic = row
            members = [post]
            if post.grouped_id is not None:
                members = list(
                    (
                        await session.execute(
                            select(Post)
                            .where(
                                Post.channel_id == post.channel_id,
                                Post.grouped_id == post.grouped_id,
                            )
                            .order_by(Post.id)
                        )
                    ).scalars()
                )
            enrich = {
                e.post_id: e
                for e in (
                    await session.execute(
                        select(Enrichment).where(Enrichment.post_id.in_([m.id for m in members]))
                    )
                ).scalars()
            }
            neighbours: list[dict[str, Any]] = []
            pc = (
                await session.execute(select(PostCluster).where(PostCluster.post_id == post.id))
            ).scalar_one_or_none()
            if pc is not None:
                cluster = await session.get(Cluster, pc.cluster_id)
                others = (
                    await session.execute(
                        select(Post.id, Channel.username, Post.posted_at)
                        .join(PostCluster, PostCluster.post_id == Post.id)
                        .join(Channel, Channel.id == Post.channel_id)
                        .where(PostCluster.cluster_id == pc.cluster_id, Post.id != post.id)
                    )
                ).all()
                neighbours = [
                    {"post_id": int(i), "channel": u, "date": d.strftime("%Y-%m-%d")}
                    for i, u, d in others
                ]
                cluster_info = {
                    "cluster_id": pc.cluster_id,
                    "size": cluster.size if cluster else len(others) + 1,
                    "representative": cluster.representative_post_id if cluster else None,
                }
            else:
                cluster_info = None
        head = next((enrich[m.id] for m in members if m.id in enrich), None)
        return {
            "post_id": post.id,
            "channel": username,
            "topic": topic,
            "date": post.posted_at.strftime("%Y-%m-%d"),
            "url": f"https://t.me/{username}/{post.tg_message_id}",
            "text": "\n".join(m.text for m in members if m.text)[:4000],
            "media": [m.media_type for m in members if m.media_type],
            "views": post.views,
            "reactions": post.reactions_count,
            "ocr": [
                enrich[m.id].ocr_text for m in members if m.id in enrich and enrich[m.id].ocr_text
            ],
            "captions": [
                enrich[m.id].vlm_caption
                for m in members
                if m.id in enrich and enrich[m.id].vlm_caption
            ],
            "label": head.topic_labels[0] if head and head.topic_labels else None,
            "is_ad": head.is_ad if head else None,
            "is_spoiler": head.is_spoiler if head else None,
            "quality": head.quality_score if head else None,
            "entities": head.entities_json if head else None,
            "link_summary": head.link_summary if head else None,
            "cluster": cluster_info,
            "duplicates": neighbours,
        }

    async def list_channels(args: BaseModel) -> Any:
        a = ChannelsArgs.model_validate(args.model_dump())
        async with deps.factory() as session:
            stmt = (
                select(Channel, func.count(Post.id))
                .outerjoin(Post, Post.channel_id == Channel.id)
                .where(Channel.is_active)
                .group_by(Channel.id)
                .order_by(Channel.topic, Channel.username)
            )
            if a.topic:
                stmt = stmt.where(Channel.topic == a.topic)
            rows = (await session.execute(stmt)).all()
        return [
            {
                "username": c.username,
                "title": c.title,
                "topic": c.topic,
                "subscribers": c.subscribers,
                "posts": int(n),
            }
            for c, n in rows
        ]

    async def web_search(args: BaseModel) -> Any:
        a = WebSearchArgs.model_validate(args.model_dump())
        try:
            resp = await deps.http.get(
                WIKIPEDIA_API.format(lang=a.lang),
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": a.query,
                    "srlimit": a.limit,
                    "format": "json",
                    "utf8": 1,
                },
            )
        except httpx.TimeoutException as exc:
            raise ToolError("timeout", "wikipedia did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ToolError("unavailable", f"wikipedia: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ToolError("unavailable", f"wikipedia answered {resp.status_code}")
        items = resp.json().get("query", {}).get("search", [])
        return {
            "source": f"{a.lang}.wikipedia.org",
            "results": [
                {
                    "title": it["title"],
                    "snippet": _strip_tags(it.get("snippet", ""))[:300],
                    "url": f"https://{a.lang}.wikipedia.org/wiki/{it['title'].replace(' ', '_')}",
                }
                for it in items
            ],
        }

    async def fetch_url(args: BaseModel) -> Any:
        a = FetchArgs.model_validate(args.model_dump())
        page = await fetch_page(deps.http, a.url)
        if page.error:
            kind = "timeout" if page.error == "timeout" else "unavailable"
            raise ToolError(kind, f"{a.url}: {page.error}")
        return {"url": page.final_url or a.url, "title": page.title, "text": page.text[:6000]}

    async def lookup_film(args: BaseModel) -> Any:
        a = FilmArgs.model_validate(args.model_dump())
        result = await deps.grounder.film(
            FilmMention(title=a.title, year=a.year, original_title=a.original_title)
        )
        if result.get("unresolved"):
            raise ToolError("not_found", f"no film matched {a.title!r}")
        return result

    async def get_profile(args: BaseModel) -> Any:
        a = ProfileArgs.model_validate(args.model_dump())
        async with deps.factory() as session:
            profile = await session.get(UserProfile, a.user_id)
        if profile is None:
            return {"user_id": a.user_id, "topic_weights": {}, "negative_prefs": [], "new": True}
        return {
            "user_id": a.user_id,
            "topic_weights": profile.topic_weights_json,
            "channel_affinity": profile.channel_affinity_json,
            "negative_prefs": profile.negative_prefs,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }

    async def update_profile(args: BaseModel) -> Any:
        a = ProfileChange.model_validate(args.model_dump())
        async with deps.factory() as session:
            profile = await session.get(UserProfile, a.user_id)
            if profile is None:
                profile = UserProfile(user_id=a.user_id)
                session.add(profile)
            if a.topic_weights:
                bad = {k: v for k, v in a.topic_weights.items() if not 0 <= v <= 1}
                if bad:
                    raise ToolError("invalid_args", f"weights must be within [0, 1]: {bad}")
                profile.topic_weights_json = {
                    **(profile.topic_weights_json or {}),
                    **a.topic_weights,
                }
            if a.negative_prefs:
                current = list(profile.negative_prefs or [])
                profile.negative_prefs = current + [p for p in a.negative_prefs if p not in current]
            await session.commit()
            return {
                "user_id": a.user_id,
                "topic_weights": profile.topic_weights_json,
                "negative_prefs": profile.negative_prefs,
            }

    tools = [
        Tool(
            "search_index",
            "Search the Telegram post index (sci-fi, cinema, humour). Hybrid semantic + "
            "lexical search over post text, OCR and image captions; ads excluded, duplicates "
            "collapsed. Use post-like wording, keep dates/channels in the filters.",
            SearchArgs,
            search_index,
            timeout_s=60,
        ),
        Tool(
            "get_post",
            "Full post by id: text, media, OCR, captions, labels, entities, link summary, its "
            "duplicate cluster and the t.me link.",
            PostArgs,
            get_post,
        ),
        Tool(
            "list_channels",
            "Channels in the corpus with topic and post counts.",
            ChannelsArgs,
            list_channels,
        ),
        Tool(
            "web_search",
            "External check via Wikipedia search (ru or en): titles, snippets, links. Not a "
            "general web engine.",
            WebSearchArgs,
            web_search,
        ),
        Tool(
            "fetch_url",
            "Fetch a web page and return its readable text (articles only; videos, paywalls "
            "and non-HTML fail explicitly).",
            FetchArgs,
            fetch_url,
            timeout_s=45,
        ),
        Tool(
            "lookup_film",
            "Ground a film in Wikidata: year, director, original title, rating hints. "
            "For cinema questions where the exact film matters.",
            FilmArgs,
            lookup_film,
        ),
        Tool(
            "get_profile",
            "The user's interest profile (topic weights, exclusions).",
            ProfileArgs,
            get_profile,
        ),
        Tool(
            "update_profile",
            "Change the user's profile: merge topic weights, add exclusions. Requires the "
            "user's confirmation.",
            ProfileChange,
            update_profile,
            writes=True,
        ),
    ]
    return ToolRegistry(tools, confirmer=confirmer)


def _strip_tags(s: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", s).replace("&quot;", '"').replace("&amp;", "&")


def make_http(timeout_s: float = 20.0, proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        proxy=proxy,
        headers={"User-Agent": "tgdigest-agent/0.1 (+https://github.com/puleon/tg-digest-agent)"},
    )


__all__: Sequence[str] = (
    "ChannelsArgs",
    "FetchArgs",
    "FilmArgs",
    "PostArgs",
    "ProfileArgs",
    "ProfileChange",
    "SearchArgs",
    "ToolDeps",
    "WebSearchArgs",
    "build_registry",
    "make_http",
)
