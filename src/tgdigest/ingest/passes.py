"""Second-stage enrichment passes over posts that already carry base enrichment.

Each pass owns one ``enrichment`` column, embeds its own version tag in the stored JSON and
selects pending posts by that column being NULL — idempotent and independently re-runnable,
without touching the (expensive) vision/classification stage or its ``model_version``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.ingest import entities, links
from tgdigest.ingest.normalize import extract_urls, normalize_text
from tgdigest.llm.client import LLMClient

log = structlog.get_logger(__name__)


@dataclass
class PassStats:
    name: str
    selected: int = 0
    processed: int = 0
    with_result: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failed: list[int] = field(default_factory=list)
    detail: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.detail[key] = self.detail.get(key, 0) + 1


async def _pending(
    session: AsyncSession,
    column: Any,
    *,
    topic: str | None,
    limit: int | None,
    force: bool,
    since: datetime | None = None,
) -> list[Post]:
    """Album captions live on the first member; passes run on posts that have text."""
    stmt = (
        select(Post)
        .join(Enrichment, Enrichment.post_id == Post.id)
        .join(Channel, Channel.id == Post.channel_id)
        .options(selectinload(Post.channel), selectinload(Post.enrichment))
        .where(Post.text != "")
        .order_by(Post.posted_at.desc(), Post.id)
    )
    if not force:
        stmt = stmt.where(column.is_(None))
    if topic:
        stmt = stmt.where(Channel.topic == topic)
    if since is not None:
        stmt = stmt.where(Post.posted_at >= since)
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().unique())


def _body(post: Post) -> str:
    parts = [normalize_text(post.text)]
    enr = post.enrichment
    if enr is not None:
        if enr.ocr_text:
            parts.append(f"[text in image: {enr.ocr_text}]")
        if enr.vlm_caption:
            parts.append(f"[image: {enr.vlm_caption}]")
    return "\n".join(p for p in parts if p)


async def run_links(
    factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    *,
    topic: str | None = None,
    limit: int | None = None,
    force: bool = False,
    concurrency: int = 2,
    proxy: str | None = None,
    since: datetime | None = None,
) -> PassStats:
    stats = PassStats("links")
    async with factory() as session:
        posts = await _pending(
            session, Enrichment.external_links, topic=topic, limit=limit, force=force, since=since
        )
        todo = [(p.id, extract_urls(p.text)) for p in posts]
    stats.selected = len(todo)
    sem = asyncio.Semaphore(concurrency)
    version = links.link_version()

    async with links.make_client(proxy) as client:

        async def one(post_id: int, urls: list[str]) -> None:
            selected = links.select_links(urls)
            async with sem:
                try:
                    if selected:
                        result = await links.enrich_post_links(llm, client, selected)
                        payload: dict[str, Any] = {"version": version, "items": result.items}
                        summary = result.summary
                        stats.prompt_tokens += result.usage.prompt_tokens
                        stats.completion_tokens += result.usage.completion_tokens
                        for item in result.items:
                            stats.bump(item.get("error") or "ok")
                    else:
                        payload, summary = {"version": version, "items": []}, None
                        stats.bump("no_external_links")
                    async with factory() as session:
                        await session.execute(
                            update(Enrichment)
                            .where(Enrichment.post_id == post_id)
                            .values(external_links=payload, link_summary=summary)
                        )
                        await session.commit()
                except Exception as exc:  # one post must never stop the pass
                    log.error("links_failed", post_id=post_id, error=repr(exc)[:300])
                    stats.failed.append(post_id)
                    return
                stats.processed += 1
                stats.with_result += summary is not None

        await asyncio.gather(*(one(pid, urls) for pid, urls in todo))
    log.info("links_done", **{k: v for k, v in vars(stats).items() if k != "failed"})
    return stats


async def run_entities(
    factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    *,
    topic: str | None = None,
    limit: int | None = None,
    force: bool = False,
    concurrency: int = 2,
    since: datetime | None = None,
) -> PassStats:
    """Films/books/people for posts labelled (or from channels of) cinema and scifi."""
    stats = PassStats("entities")
    async with factory() as session:
        posts = await _pending(
            session, Enrichment.entities_json, topic=topic, limit=limit, force=force, since=since
        )
        todo: list[tuple[int, str, int | None]] = []
        for p in posts:
            labels = p.enrichment.topic_labels if p.enrichment else None
            label = labels[0] if labels else None
            if label in ("cinema", "scifi") or p.channel.topic in ("cinema", "scifi"):
                todo.append((p.id, _body(p), p.posted_at.year))
            else:
                todo.append((p.id, "", None))  # not a grounding topic: mark done, empty result
    stats.selected = len(todo)
    sem = asyncio.Semaphore(concurrency)
    cache = entities.DbCache(factory)

    async with entities.make_client() as client:
        grounder = entities.Grounder(client, cache)

        async def one(post_id: int, body: str, year: int | None) -> None:
            async with sem:
                try:
                    if not body:
                        payload: dict[str, Any] = {
                            "version": entities.entities_version(),
                            "skipped": True,
                        }
                        stats.bump("skipped_topic")
                    else:
                        mentions, usage = await entities.extract_mentions(llm, body)
                        stats.prompt_tokens += usage.prompt_tokens
                        stats.completion_tokens += usage.completion_tokens
                        if mentions is None:
                            payload = {"version": entities.entities_version(), "failed": True}
                            stats.bump("extract_failed")
                        else:
                            payload = await grounder.ground(mentions, year)
                            stats.bump("ok")
                            for f in payload["films"]:
                                stats.bump(
                                    "film_unresolved" if f.get("unresolved") else "film_resolved"
                                )
                            for b in payload["books"]:
                                stats.bump(
                                    "book_unresolved" if b.get("unresolved") else "book_resolved"
                                )
                    async with factory() as session:
                        await session.execute(
                            update(Enrichment)
                            .where(Enrichment.post_id == post_id)
                            .values(entities_json=payload)
                        )
                        await session.commit()
                except Exception as exc:
                    log.error("entities_failed", post_id=post_id, error=repr(exc)[:300])
                    stats.failed.append(post_id)
                    return
                stats.processed += 1
                stats.with_result += bool(payload.get("films") or payload.get("books"))

        await asyncio.gather(*(one(pid, body, year) for pid, body, year in todo))
    log.info("entities_done", **{k: v for k, v in vars(stats).items() if k != "failed"})
    return stats
