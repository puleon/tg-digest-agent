"""External-link enrichment (SPEC §6.2 step 4): fetch, extract, summarize in 2–3 sentences.

Handled explicitly: 404 and other HTTP errors, timeouts, non-HTML responses, oversized pages,
paywalls/login walls and empty shells (``relevant=false`` from the model or a text-length
heuristic), and hosts this box cannot reach (video sites are recorded, never fetched).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from tgdigest.llm.client import LLMClient, LLMError, LLMOutputError, Usage
from tgdigest.prompts import load_prompt, prompt_id

log = structlog.get_logger(__name__)

SUMMARY_PROMPT = ("summarize_link", 1)
MAX_LINKS_PER_POST = 2
MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 6000
MIN_ARTICLE_CHARS = 300
USER_AGENT = "tg-digest-agent/0.1 (+https://github.com/puleon/tg-digest-agent; link previews)"
SKIP_HOSTS = ("t.me", "telegram.me", "telegram.org")
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "rutube.ru", "vk.com/video")
_PAYWALL_HINTS = re.compile(
    r"подпис\w*|paywall|subscri|sign in|log in|войти|зарегистрир|premium|платн", re.I
)
_DROP_TAGS = "script, style, noscript, nav, header, footer, aside, form, iframe, svg"


class LinkSummary(BaseModel):
    relevant: bool = True
    summary: str = Field(default="", max_length=800)


@dataclass
class PageResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    title: str = ""
    text: str = ""
    error: str | None = None
    """None on success, otherwise: video | skipped | http_<code> | timeout | not_html |
    too_large | unreachable | paywall_or_stub."""


def link_version() -> str:
    return prompt_id(*SUMMARY_PROMPT)


def select_links(urls: list[str]) -> list[str]:
    """Keep the first few distinct external links; Telegram-internal ones are not links out."""
    out: list[str] = []
    for url in urls:
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
        if not host or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        if url not in out:
            out.append(url)
        if len(out) >= MAX_LINKS_PER_POST:
            break
    return out


def is_video_link(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in VIDEO_HOSTS)


def extract_page_text(html: str) -> tuple[str, str]:
    """(title, readable text): boilerplate tags dropped, block texts joined, capped."""
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    og = tree.css_first('meta[property="og:title"]')
    og_title = og.attributes.get("content") if og is not None else None
    if og_title:
        title = og_title.strip()
    for node in tree.css(_DROP_TAGS):
        node.decompose()
    root = tree.css_first("article") or tree.css_first("main") or tree.body or tree.root
    if root is None:
        return title, ""
    blocks = []
    for node in root.css("h1, h2, h3, p, li, blockquote"):
        t = node.text(separator=" ", strip=True)
        if len(t) >= 30:
            blocks.append(t)
    text = "\n".join(blocks)
    if len(text) < MIN_ARTICLE_CHARS:  # no paragraph structure: fall back to everything
        text = root.text(separator="\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return title, text[:MAX_TEXT_CHARS]


async def fetch_page(client: httpx.AsyncClient, url: str) -> PageResult:
    if is_video_link(url):
        return PageResult(url=url, error="video")
    full = url if "://" in url else f"https://{url}"
    try:
        async with client.stream("GET", full) as resp:
            result = PageResult(url=url, final_url=str(resp.url), status=resp.status_code)
            if resp.status_code >= 400:
                result.error = f"http_{resp.status_code}"
                return result
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                result.error = "not_html"
                return result
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    result.error = "too_large"
                    return result
                chunks.append(chunk)
    except httpx.TimeoutException:
        return PageResult(url=url, error="timeout")
    except httpx.HTTPError as exc:
        return PageResult(url=url, error=f"unreachable:{type(exc).__name__}")
    html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    result.title, result.text = extract_page_text(html)
    if len(result.text) < MIN_ARTICLE_CHARS and _PAYWALL_HINTS.search(html[:20000]):
        result.error = "paywall_or_stub"
    elif len(result.text) < 80:
        result.error = "empty"
    return result


async def summarize_page(llm: LLMClient, page: PageResult) -> tuple[LinkSummary | None, Usage]:
    body = f"<page url={page.final_url or page.url!r} title={page.title!r}>\n{page.text}\n</page>"
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": load_prompt(*SUMMARY_PROMPT)},
        {"role": "user", "content": body},
    ]
    try:
        summary, completion = await llm.structured(
            messages, LinkSummary, tier="fast", max_tokens=220
        )
    except (LLMOutputError, LLMError) as exc:
        log.warning("summarize_failed", url=page.url, error=repr(exc)[:200])
        return None, Usage()
    return summary, completion.usage


@dataclass
class LinkEnrichment:
    items: list[dict[str, Any]]
    summary: str | None
    usage: Usage


async def enrich_post_links(
    llm: LLMClient, client: httpx.AsyncClient, urls: list[str]
) -> LinkEnrichment:
    items: list[dict[str, Any]] = []
    summaries: list[str] = []
    usage = Usage()
    for url in select_links(urls):
        page = await fetch_page(client, url)
        item = {k: v for k, v in asdict(page).items() if k != "text"}
        if page.error is None:
            summary, used = await summarize_page(llm, page)
            usage = usage + used
            if summary is None:
                item["error"] = "summarize_failed"
            elif not summary.relevant:
                item["error"] = "irrelevant"
            else:
                item["summary"] = summary.summary
                summaries.append(summary.summary)
        items.append(item)
        await asyncio.sleep(0)
    return LinkEnrichment(items=items, summary="\n".join(summaries) or None, usage=usage)


def make_client(proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        timeout=12.0,
        follow_redirects=True,
        proxy=proxy,
    )
