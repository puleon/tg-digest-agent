from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from tests.ingest.conftest import FakeLLM
from tgdigest.ingest.entities import (
    BookMention,
    Cache,
    EntityMentions,
    FilmMention,
    Grounder,
    extract_mentions,
    title_similarity,
)
from tgdigest.llm.client import Completion, Usage

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text(encoding="utf-8"))
    return data


class Routes:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, q = str(request.url), request.url.params
        self.calls.append(url)
        if "wikidata" in url and q.get("action") == "wbsearchentities":
            return httpx.Response(
                200,
                json=_load("wd_search_solaris_ru.json")
                if "оляр" in q.get("search", "") or "olaris" in q.get("search", "")
                else {"search": []},
            )
        if "wikidata" in url and q.get("action") == "wbgetentities":
            if q.get("props") == "labels":
                return httpx.Response(200, json=_load("wd_directors.json"))
            return httpx.Response(200, json=_load("wd_entities_solaris.json"))
        if "fantlab" in url:
            return httpx.Response(
                200,
                json=_load("fantlab_solaris.json") if "оляр" in q.get("q", "") else {"matches": []},
            )
        if "openlibrary" in url:
            return httpx.Response(200, json=_load("openlibrary_solaris.json"))
        return httpx.Response(404)


def _grounder() -> tuple[Grounder, Routes]:
    routes = Routes()
    client = httpx.AsyncClient(transport=httpx.MockTransport(routes.handler))
    return Grounder(client, Cache()), routes


def test_title_similarity() -> None:
    assert title_similarity("Солярис", "Солярис") == 1.0
    assert title_similarity("Сталкер (1979)", "Сталкер") == 0.5
    assert title_similarity("", "x") == 0.0


async def test_film_year_disambiguates_between_adaptations() -> None:
    g, _ = _grounder()
    tark = await g.film(FilmMention(title="Солярис", year=1972))
    assert tark["wikidata_id"] == "Q125772" and tark["year"] == 1972 and tark["kind"] == "film"
    assert tark["directors"] == ["Андрей Тарковский"] and tark["imdb_id"]
    soder = await g.film(FilmMention(title="Солярис", year=2002))
    assert soder["wikidata_id"] == "Q673195" and soder["directors"] == ["Стивен Содерберг"]


async def test_film_without_year_prefers_a_film_over_the_novel_and_the_club() -> None:
    g, _ = _grounder()
    out = await g.film(FilmMention(title="Солярис"))
    assert out["kind"] in {"film", "television film"} and out["wikidata_id"] != "Q261281"


async def test_unknown_film_stays_unresolved_and_is_cached() -> None:
    g, routes = _grounder()
    out = await g.film(FilmMention(title="Несуществующий фильм"))
    assert out["unresolved"] is True
    n = len(routes.calls)
    again = await g.film(FilmMention(title="Несуществующий фильм"))
    assert again == out and len(routes.calls) == n  # cache hit: no new requests


async def test_book_resolves_on_fantlab_with_author_boost() -> None:
    g, _ = _grounder()
    out = await g.book(BookMention(title="Солярис", author="Станислав Лем"))
    assert out["provider"] == "fantlab" and out["author"] == "Станислав Лем" and out["year"] == 1961
    assert out["fantlab_id"]


async def test_book_falls_back_to_open_library() -> None:
    g, routes = _grounder()
    out = await g.book(BookMention(title="Solaris", author="Lem"))
    assert out["provider"] == "openlibrary" and out["year"] == 1961
    assert any("openlibrary" in c for c in routes.calls)


class MentionLLM(FakeLLM):
    async def structured(self, messages, schema, *, tier="fast", **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"tier": tier, "schema": schema.__name__, "messages": messages, **kw})
        return (
            EntityMentions(
                films=[FilmMention(title="Солярис", year=1972)],
                books=[BookMention(title="Солярис", author="Станислав Лем")],
                people=["Андрей Тарковский"],
            ),
            Completion(text="{}", model="fast-m", usage=Usage(50, 20)),
        )


async def test_extract_and_ground_end_to_end() -> None:
    llm = MentionLLM()
    mentions, usage = await extract_mentions(llm, "Пересмотрел «Солярис» Тарковского (1972)...")  # type: ignore[arg-type]
    assert mentions is not None and usage.prompt_tokens == 50
    assert "<post>" in llm.calls[0]["messages"][-1]["content"]
    g, _ = _grounder()
    payload = await g.ground(mentions)
    assert payload["version"].startswith("extract_entities.v1")
    assert payload["films"][0]["wikidata_id"] == "Q125772"
    assert payload["books"][0]["provider"] == "fantlab"
    assert payload["people"] == ["Андрей Тарковский"]


async def test_post_year_hint_prefers_the_current_release() -> None:
    g, _ = _grounder()
    recent = await g.film(FilmMention(title="Солярис"), hint_year=2002)
    assert recent["wikidata_id"] == "Q673195"  # Soderbergh, 2002
    old = await g.film(FilmMention(title="Солярис"), hint_year=1972)
    assert old["wikidata_id"] == "Q125772"  # Tarkovsky, 1972
