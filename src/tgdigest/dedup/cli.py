"""``tgdigest dedup …``"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Deduplication (M3)")


@app.command()
def signatures(
    recompute: Annotated[
        bool, typer.Option(help="drop existing signatures first (after a rule change)")
    ] = False,
) -> None:
    """Compute text/file/perceptual signatures for posts that lack them."""
    from sqlalchemy import delete

    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.db.models import PostSignature
    from tgdigest.dedup.runner import compute_signatures

    settings = get_settings()
    configure_logging(settings.log_level)

    async def go() -> None:
        engine = make_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            if recompute:
                async with factory() as session:
                    await session.execute(delete(PostSignature))
                    await session.commit()
            stats = await compute_signatures(factory, settings.media_dir)
        finally:
            await engine.dispose()
        typer.echo(
            f"selected={stats.selected} computed={stats.computed} text={stats.with_text} "
            f"file={stats.with_file} phash={stats.with_phash}"
        )

    asyncio.run(go())


@app.command()
def run(
    phash_threshold: Annotated[int, typer.Option(help="max Hamming distance for pHash edges")] = 6,
    max_frequency: Annotated[
        int, typer.Option(help="a hash shared by more posts than this is a placeholder")
    ] = 12,
) -> None:
    """Rebuild duplicate clusters from signatures and forwards (idempotent)."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.dedup.runner import rebuild_clusters

    settings = get_settings()
    configure_logging(settings.log_level)

    async def go() -> None:
        engine = make_engine(settings.database_url)
        try:
            stats = await rebuild_clusters(
                make_session_factory(engine),
                phash_threshold=phash_threshold,
                max_signature_frequency=max_frequency,
            )
        finally:
            await engine.dispose()
        typer.echo(
            f"posts={stats.posts} clusters={stats.clusters} "
            f"clustered_posts={stats.clustered_posts} largest={stats.largest} "
            f"edges_by_stage={stats.stage_counts}"
        )

    asyncio.run(go())


@app.command()
def stats() -> None:
    """Clusters per topic."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.dedup.runner import cluster_stats

    settings = get_settings()

    async def go() -> None:
        engine = make_engine(settings.database_url)
        try:
            async with make_session_factory(engine)() as session:
                rows = await cluster_stats(session)
        finally:
            await engine.dispose()
        for topic, n, members in rows:
            typer.echo(f"{topic:7s} clusters={n:5d} posts_in_clusters={members:6d}")

    asyncio.run(go())
