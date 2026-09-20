from __future__ import annotations

import httpx
import pytest

from tests.ingest.conftest import FakeLLM
from tgdigest.ingest.links import (
    LinkSummary,
    enrich_post_links,
    extract_page_text,
    fetch_page,
    is_video_link,
    select_links,
)

ARTICLE = (
    '<html><head><title>Тест</title><meta property="og:title" content="Лем и «Солярис»"></head>'
    "<body><nav>Главная Новости Подписка</nav><script>var x=1;</script>"
    "<article><h1>Почему «Солярис» не о контакте</h1>"
    "<p>Станислав Лем писал роман о пределах человеческого познания, а не о встрече с чужим "
    "разумом, и океан планеты отвечает людям их же страхами.</p>"
    "<p>Тарковский, экранизируя книгу, сместил акцент с эпистемологии на совесть и память, "
    "и с тех пор спор о том, чья версия честнее, не утихает.</p>"
    "<p>Короткая строка.</p></article><footer>© 2026</footer></body></html>"
)
PAYWALL = (
    "<html><head><title>Статья</title></head>"
    "<body><p>Чтобы читать дальше, оформите подписку.</p></body></html>"
)
HTML = {"content-type": "text/html"}


def test_select_links_skips_telegram_and_caps() -> None:
    urls = [
        "https://t.me/x/1",
        "https://mirf.ru/a",
        "https://mirf.ru/a",
        "www.seance.ru/b",
        "https://c.ru",
    ]
    assert select_links(urls) == ["https://mirf.ru/a", "www.seance.ru/b"]
    assert is_video_link("https://youtu.be/abc") and not is_video_link("https://mirf.ru/x")


def test_extract_page_text_drops_boilerplate() -> None:
    title, text = extract_page_text(ARTICLE)
    assert title == "Лем и «Солярис»"
    assert text.startswith("Почему «Солярис» не о контакте\nСтанислав Лем")
    assert "Подписка" not in text and "var x" not in text and "Короткая строка" not in text


def _client(responses: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return responses.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_fetch_page_classifies_failures() -> None:
    client = _client(
        {
            "https://ok.ru/a": httpx.Response(
                200, text=ARTICLE, headers={"content-type": "text/html"}
            ),
            "https://pay.ru/a": httpx.Response(
                200, text=PAYWALL, headers={"content-type": "text/html"}
            ),
            "https://pdf.ru/a": httpx.Response(
                200, content=b"%PDF", headers={"content-type": "application/pdf"}
            ),
            "https://gone.ru/a": httpx.Response(404, text="nope"),
        }
    )
    assert (await fetch_page(client, "https://ok.ru/a")).error is None
    assert (await fetch_page(client, "https://pay.ru/a")).error == "paywall_or_stub"
    assert (await fetch_page(client, "https://pdf.ru/a")).error == "not_html"
    assert (await fetch_page(client, "https://gone.ru/a")).error == "http_404"
    assert (await fetch_page(client, "https://youtu.be/x")).error == "video"
    assert (await fetch_page(client, "https://missing.ru/a")).error == "http_404"


async def test_fetch_page_timeout_and_size_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "slow" in str(request.url):
            raise httpx.ReadTimeout("slow")
        return httpx.Response(
            200, content=b"<html>" + b"x" * 1_100_000, headers={"content-type": "text/html"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await fetch_page(client, "https://slow.ru/a")).error == "timeout"
    assert (await fetch_page(client, "https://big.ru/a")).error == "too_large"


class SummaryLLM(FakeLLM):
    async def structured(self, messages, schema, *, tier="fast", **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"tier": tier, "schema": schema.__name__, "messages": messages, **kw})
        from tgdigest.llm.client import Completion, Usage

        body = messages[-1]["content"]
        relevant = "Лем" in body
        return (
            LinkSummary(
                relevant=relevant, summary="Лем писал о пределах познания." if relevant else ""
            ),
            Completion(text="{}", model="fast-m", usage=Usage(30, 8)),
        )


async def test_enrich_post_links_summarizes_only_good_pages() -> None:
    client = _client(
        {
            "https://ok.ru/a": httpx.Response(
                200, text=ARTICLE, headers={"content-type": "text/html"}
            ),
            "https://shop.ru/x": httpx.Response(
                200,
                text="<html><body><p>" + "Купить билет. " * 40 + "</p></body></html>",
                headers={"content-type": "text/html"},
            ),
        }
    )
    llm = SummaryLLM()
    urls = ["https://ok.ru/a", "https://shop.ru/x", "https://t.me/z"]
    out = await enrich_post_links(llm, client, urls)  # type: ignore[arg-type]
    assert [i["url"] for i in out.items] == ["https://ok.ru/a", "https://shop.ru/x"]
    assert out.items[0]["summary"] == "Лем писал о пределах познания." and out.items[0]["title"]
    assert out.items[1]["error"] == "irrelevant" and "summary" not in out.items[1]
    assert out.summary == "Лем писал о пределах познания."
    assert out.usage.prompt_tokens == 60 and len(llm.calls) == 2
    assert "<page" in llm.calls[0]["messages"][-1]["content"]


@pytest.mark.parametrize("url", ["https://youtu.be/x", "https://www.youtube.com/watch?v=1"])
async def test_video_links_are_recorded_not_fetched(url: str) -> None:
    llm = SummaryLLM()
    out = await enrich_post_links(llm, _client({}), [url])  # type: ignore[arg-type]
    assert out.items[0]["error"] == "video" and out.summary is None and llm.calls == []
