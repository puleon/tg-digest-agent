"""``tgdigest ingest …``"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Ingest agent (M2)")


@app.command()
def run(
    topic: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option()] = None,
    force: Annotated[
        bool, typer.Option(help="re-enrich even if this model_version exists")
    ] = False,
    concurrency: int = 2,
) -> None:
    """Enrich posts that have no enrichment for the current model_version."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.ingest.graph import IngestDeps
    from tgdigest.ingest.runner import run_ingest
    from tgdigest.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level)

    async def go() -> None:
        engine = make_engine(settings.database_url)
        try:
            deps = IngestDeps(llm=LLMClient(settings), media_root=settings.media_dir)
            stats = await run_ingest(
                make_session_factory(engine),
                deps,
                topic=topic,
                limit=limit,
                force=force,
                concurrency=concurrency,
            )
        finally:
            await engine.dispose()
        typer.echo(
            f"model_version={stats.model_version}\nselected={stats.selected} "
            f"processed={stats.processed} failed={len(stats.failed)} "
            f"tokens={stats.prompt_tokens}+{stats.completion_tokens} degraded={stats.degraded}"
        )

    asyncio.run(go())


@app.command()
def stats() -> None:
    """Enrichment coverage per model_version and topic."""
    from sqlalchemy import Integer, cast, func, select

    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.db.models import Channel, Enrichment, Post

    settings = get_settings()

    async def go() -> None:
        engine = make_engine(settings.database_url)
        try:
            async with make_session_factory(engine)() as session:
                total = (await session.execute(select(func.count(Post.id)))).scalar_one()
                stmt = (
                    select(
                        Enrichment.model_version,
                        Channel.topic,
                        func.count(Enrichment.post_id),
                        func.count(Enrichment.ocr_text),
                        func.sum(cast(Enrichment.is_ad, Integer)),
                        func.sum(cast(Enrichment.injection_flag, Integer)),
                    )
                    .join(Post, Post.id == Enrichment.post_id)
                    .join(Channel, Channel.id == Post.channel_id)
                    .group_by(Enrichment.model_version, Channel.topic)
                    .order_by(Enrichment.model_version, Channel.topic)
                )
                rows = (await session.execute(stmt)).all()
        finally:
            await engine.dispose()
        typer.echo(f"posts: {total}")
        for version, topic, n, with_ocr, ads, inj in rows:
            typer.echo(
                f"{version}  {topic:7s} enriched={n} ocr={with_ocr} ads={ads} injection={inj}"
            )

    asyncio.run(go())
