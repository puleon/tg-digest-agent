"""Entity grounding (SPEC §6.2 step 5): films → Wikidata, books → FantLab (Open Library fallback).

TMDB is what the spec names for cinema, but this box cannot reach it; Wikidata gives the same
grounding fields (year, director, IMDb id) with no key. FantLab is the Russian SF database, so
it resolves the scifi corpus's book titles far better than a Western catalogue would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.db.models import EntityCache
from tgdigest.db.upsert import insert_or_update
from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, Usage
from tgdigest.prompts import load_prompt, prompt_id

log = structlog.get_logger(__name__)

EXTRACT_PROMPT = ("extract_entities", 1)
USER_AGENT = "tg-digest-agent/0.1 (+https://github.com/puleon/tg-digest-agent; entity grounding)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
FANTLAB_API = "https://api.fantlab.ru/search-works"
OPENLIBRARY_API = "https://openlibrary.org/search.json"
FILM_CLASSES = {
    "Q11424": "film",
    "Q24869": "feature film",
    "Q202866": "animated film",
    "Q506240": "television film",
    "Q5398426": "television series",
    "Q1259759": "miniseries",
    "Q93204": "documentary film",
    "Q226730": "silent film",
    "Q20650540": "anime film",
    "Q63952888": "anime series",
}
MAX_MENTIONS = 5


class FilmMention(BaseModel):
    title: str
    year: int | None = None
    original_title: str | None = None


class BookMention(BaseModel):
    title: str
    author: str | None = None


class EntityMentions(BaseModel):
    films: list[FilmMention] = Field(default_factory=list)
    books: list[BookMention] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)


def entities_version() -> str:
    return f"{prompt_id(*EXTRACT_PROMPT)}|wikidata+fantlab"


def _tokens(s: str) -> set[str]:
    return set(re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е")).split())


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def extract_mentions(llm: LLMClient, body: str) -> tuple[EntityMentions | None, Usage]:
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": load_prompt(*EXTRACT_PROMPT)},
        {"role": "user", "content": f"<post>\n{body}\n</post>"},
    ]
    try:
        mentions, completion = await llm.structured(
            messages, EntityMentions, tier="fast", max_tokens=300
        )
    except (LLMOutputError, LLMError) as exc:
        log.warning("extract_entities_failed", error=repr(exc)[:200])
        return None, Usage()
    return mentions, completion.usage


class Cache:
    """Provider lookups are deterministic and slow: memoize per (provider, query)."""

    def __init__(self) -> None:
        self._mem: dict[tuple[str, str], dict[str, Any]] = {}

    async def get(self, provider: str, query: str) -> dict[str, Any] | None:
        value = self._mem.get((provider, query))
        return dict(value) if value is not None else None

    async def put(self, provider: str, query: str, value: dict[str, Any]) -> None:
        self._mem[(provider, query)] = value


class DbCache(Cache):
    """Same, persisted in ``entity_cache`` so re-runs and later days never repeat a lookup."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self._factory = factory

    async def get(self, provider: str, query: str) -> dict[str, Any] | None:
        hit = await super().get(provider, query)
        if hit is not None:
            return hit
        async with self._factory() as session:
            row = await session.get(EntityCache, (provider, query[:512]))
        if row is None:
            return None
        await super().put(provider, query, row.result_json)
        return dict(row.result_json)

    async def put(self, provider: str, query: str, value: dict[str, Any]) -> None:
        await super().put(provider, query, value)
        async with self._factory() as session:
            await session.execute(
                insert_or_update(
                    session,
                    EntityCache.__table__,  # type: ignore[arg-type]
                    [{"provider": provider, "query": query[:512], "result_json": value}],
                    index_elements=["provider", "query"],
                    update_columns=["result_json"],
                )
            )
            await session.commit()


@dataclass
class Grounder:
    client: httpx.AsyncClient
    cache: Cache

    async def _json(self, url: str, params: dict[str, Any]) -> Any | None:
        try:
            resp = await self.client.get(url, params=params)
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("grounding_request_failed", url=url, error=repr(exc)[:120])
            return None

    # --- films: Wikidata ------------------------------------------------------------------
    async def film(self, mention: FilmMention, hint_year: int | None = None) -> dict[str, Any]:
        """``hint_year`` = the post's year: a title without a year is usually a current release
        (channels cite the year for old films), so same-title older films should not win by
        default."""
        key = (
            f"{mention.title}|{mention.year or ''}|{mention.original_title or ''}|{hint_year or ''}"
        )
        cached = await self.cache.get("wikidata_film", key)
        if cached is not None:
            return cached
        result: dict[str, Any] = {"mention": mention.model_dump(), "unresolved": True}
        candidates: list[str] = []
        for query, lang in (
            (mention.title, "ru"),
            (mention.original_title, "en"),
            (mention.title, "en"),
        ):
            if not query:
                continue
            data = await self._json(
                WIKIDATA_API,
                {
                    "action": "wbsearchentities",
                    "search": query,
                    "language": lang,
                    "uselang": lang,
                    "type": "item",
                    "limit": 7,
                    "format": "json",
                },
            )
            for item in (data or {}).get("search", []):
                if item["id"] not in candidates:
                    candidates.append(item["id"])
            if len(candidates) >= 7:
                break
        best = await self._pick_film(candidates[:10], mention, hint_year)
        if best is not None:
            result = {"mention": mention.model_dump(), **best}
        await self.cache.put("wikidata_film", key, result)
        return result

    async def _pick_film(
        self, ids: list[str], mention: FilmMention, hint_year: int | None = None
    ) -> dict[str, Any] | None:
        if not ids:
            return None
        data = await self._json(
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": "claims|labels",
                "languages": "ru|en",
                "format": "json",
            },
        )
        if not data:
            return None
        scored: list[tuple[float, dict[str, Any]]] = []
        for qid, ent in data.get("entities", {}).items():
            claims = ent.get("claims", {})
            classes = [
                c["mainsnak"]["datavalue"]["value"]["id"]
                for c in claims.get("P31", [])
                if c.get("mainsnak", {}).get("datavalue")
            ]
            kind = next((FILM_CLASSES[c] for c in classes if c in FILM_CLASSES), None)
            if kind is None:
                continue
            year = None
            for c in claims.get("P577", []):
                t = c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time")
                if t:
                    year = int(t[1:5])
                    break
            labels = ent.get("labels", {})
            title_ru = labels.get("ru", {}).get("value")
            title_en = labels.get("en", {}).get("value")
            sim = max(
                title_similarity(mention.title, title_ru or ""),
                title_similarity(mention.original_title or mention.title, title_en or ""),
            )
            score = sim
            if mention.year and year:
                score += 0.5 if abs(mention.year - year) <= 1 else -0.5
            elif hint_year and year:
                score += 0.25 if abs(hint_year - year) <= 1 else -0.15
            directors = [
                c["mainsnak"]["datavalue"]["value"]["id"]
                for c in claims.get("P57", [])
                if c.get("mainsnak", {}).get("datavalue")
            ]
            imdb = next(
                (
                    c["mainsnak"]["datavalue"]["value"]
                    for c in claims.get("P345", [])
                    if c.get("mainsnak", {}).get("datavalue")
                ),
                None,
            )
            scored.append(
                (
                    score,
                    {
                        "wikidata_id": qid,
                        "kind": kind,
                        "title": title_ru or title_en,
                        "title_en": title_en,
                        "year": year,
                        "director_ids": directors[:2],
                        "imdb_id": imdb,
                        "match": round(sim, 2),
                    },
                )
            )
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        score, best = scored[0]
        if score < 0.34:
            return None
        if best["director_ids"]:
            labels = await self._json(
                WIKIDATA_API,
                {
                    "action": "wbgetentities",
                    "ids": "|".join(best["director_ids"]),
                    "props": "labels",
                    "languages": "ru|en",
                    "format": "json",
                },
            )
            names = []
            for did in best["director_ids"]:
                lab = (labels or {}).get("entities", {}).get(did, {}).get("labels", {})
                name = lab.get("ru", {}).get("value") or lab.get("en", {}).get("value")
                if name:
                    names.append(name)
            best["directors"] = names
        best.pop("director_ids", None)
        return best

    # --- books: FantLab, then Open Library ----------------------------------------------------
    async def book(self, mention: BookMention) -> dict[str, Any]:
        key = f"{mention.title}|{mention.author or ''}"
        cached = await self.cache.get("book", key)
        if cached is not None:
            return cached
        result: dict[str, Any] = {"mention": mention.model_dump(), "unresolved": True}
        data = await self._json(FANTLAB_API, {"q": mention.title})
        best_score, best = 0.0, None
        for m in (data or {}).get("matches", [])[:15]:
            names = [m.get("rusname") or "", m.get("name") or "", m.get("altname") or ""]
            sim = max(title_similarity(mention.title, n) for n in names)
            authors = " ".join(str(m.get(k) or "") for k in ("all_autor_rusname", "all_autor_name"))
            if mention.author:
                sim += 0.3 * title_similarity(mention.author, authors)
            if sim > best_score:
                best_score, best = sim, m
        if best is not None and best_score >= 0.5:
            result = {
                "mention": mention.model_dump(),
                "provider": "fantlab",
                "fantlab_id": best.get("work_id"),
                "title": best.get("rusname") or best.get("name"),
                "title_orig": best.get("name"),
                "author": best.get("all_autor_rusname") or best.get("all_autor_name"),
                "year": best.get("year"),
                "kind": best.get("name_type") or best.get("work_type"),
                "match": round(best_score, 2),
            }
        else:
            ol = await self._json(
                OPENLIBRARY_API,
                {
                    "q": f"{mention.title} {mention.author or ''}".strip(),
                    "limit": 3,
                    "fields": "key,title,author_name,first_publish_year",
                },
            )
            for doc in (ol or {}).get("docs", []):
                sim = title_similarity(mention.title, doc.get("title", ""))
                if sim >= 0.5:
                    result = {
                        "mention": mention.model_dump(),
                        "provider": "openlibrary",
                        "openlibrary_key": doc.get("key"),
                        "title": doc.get("title"),
                        "author": ", ".join(doc.get("author_name", [])[:2]) or None,
                        "year": doc.get("first_publish_year"),
                        "match": round(sim, 2),
                    }
                    break
        await self.cache.put("book", key, result)
        return result

    async def ground(
        self, mentions: EntityMentions, hint_year: int | None = None
    ) -> dict[str, Any]:
        films = [await self.film(m, hint_year) for m in mentions.films[:MAX_MENTIONS]]
        books = [await self.book(m) for m in mentions.books[:MAX_MENTIONS]]
        return {
            "version": entities_version(),
            "films": films,
            "books": books,
            "people": mentions.people[:10],
        }


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True
    )
