"""``tgdigest index …`` — build and query the post index (M4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Retrieval layer (M4)")


def _index(threads: int | None = None):  # type: ignore[no-untyped-def]  # heavy imports
    from qdrant_client import QdrantClient

    from tgdigest.retrieval.embeddings import BGEM3Embedder
    from tgdigest.retrieval.index import PostIndex

    settings = get_settings()
    return PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=threads))


@app.command()
def build(
    topic: Annotated[str | None, typer.Option(help="scifi | humor | cinema")] = None,
    since: Annotated[datetime | None, typer.Option(help="only posts from this date on")] = None,
    limit: Annotated[int | None, typer.Option(help="max posts (debug)")] = None,
    threads: Annotated[int | None, typer.Option(help="torch threads for the embedder")] = None,
    batch: int = 256,
    recreate: Annotated[bool, typer.Option(help="drop the collection first")] = False,
) -> None:
    """Embed posts (text / +OCR / +caption / full) and upsert them; unchanged posts are skipped."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.retrieval.runner import build_index

    settings = get_settings()
    configure_logging(settings.log_level)
    since_utc = since.replace(tzinfo=UTC) if since and since.tzinfo is None else since
    index = _index(threads)
    if recreate:
        index.ensure_collection(recreate=True)

    async def go() -> None:
        engine = make_engine(settings.database_url)
        try:
            stats = await build_index(
                make_session_factory(engine),
                index,
                topic=topic,
                since=since_utc,
                limit=limit,
                batch=batch,
            )
        finally:
            await engine.dispose()
        typer.echo(
            f"posts={stats.posts} indexed={stats.indexed} unchanged={stats.unchanged} "
            f"embedded_texts={stats.embedded_texts} with_enrichment={stats.with_enrichment} "
            f"sources={stats.by_source} total_in_index={index.count()}"
        )

    asyncio.run(go())


@app.command()
def search(
    query: str,
    variant: Annotated[
        str, typer.Option(help="text | text_ocr | text_ocr_caption | full")
    ] = "full",
    mode: Annotated[str, typer.Option(help="hybrid | dense | sparse")] = "hybrid",
    topic: Annotated[str | None, typer.Option()] = None,
    since: Annotated[datetime | None, typer.Option()] = None,
    ads: Annotated[bool, typer.Option(help="include posts labelled as ads")] = False,
    spoilers: Annotated[bool, typer.Option(help="include posts labelled as spoilers")] = True,
    dedup: Annotated[bool, typer.Option(help="cluster representatives only")] = True,
    limit: int = 10,
) -> None:
    """Query the index and print the hits."""
    from tgdigest.retrieval.index import SearchFilters

    index = _index()
    filters = SearchFilters(
        topic=topic,
        date_from=since.replace(tzinfo=UTC) if since and since.tzinfo is None else since,
        exclude_ads=not ads,
        exclude_spoilers=not spoilers,
        representatives_only=dedup,
    )
    if mode not in ("hybrid", "dense", "sparse"):
        raise typer.BadParameter("mode must be hybrid | dense | sparse")
    hits = index.search(query, variant=variant, mode=mode, filters=filters, limit=limit)
    for h in hits:
        p = h.payload
        flags = "".join(
            f
            for f, on in (
                ("A", p.get("is_ad")),
                ("S", p.get("is_spoiler")),
                ("M", p.get("has_media")),
            )
            if on
        )
        snippet = (p.get("text") or "").replace("\n", " ")[:110]
        typer.echo(
            f"{h.rank:2d} {h.score:6.3f} post {h.post_id:6d} @{p.get('channel'):<22} "
            f"{p.get('date')} {p.get('label') or '-':<8} {flags:<3} {snippet}"
        )


@app.command()
def stats() -> None:
    """Points in the collection."""
    index = _index()
    typer.echo(f"collection={index.collection} points={index.count()}")
