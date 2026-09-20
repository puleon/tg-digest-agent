"""``tgdigest collector …`` / ``python -m tgdigest.collector …``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from tgdigest.config import Settings, get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Telegram collector (M1)")

CHANNELS_YAML = Path("config/channels.yaml")


def _telethon(settings: Settings):  # type: ignore[no-untyped-def]  # imported lazily: heavy
    from tgdigest.collector.telegram import TelethonSource

    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise typer.BadParameter("TELEGRAM_API_ID / TELEGRAM_API_HASH are not set in .env")
    return TelethonSource.from_settings(
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
        settings.telegram_session,
    )


def _source(settings: Settings):  # type: ignore[no-untyped-def]  # WebPreviewSource | TelethonSource
    if settings.collector_source == "web":
        from tgdigest.collector.web import WebPreviewSource

        return WebPreviewSource(delay_s=settings.web_delay_s, proxy=settings.web_proxy)
    return _telethon(settings)


@app.command()
def login() -> None:
    """One-time interactive authorization of an MTProto account (COLLECTOR_SOURCE=telethon)."""
    settings = get_settings()
    if settings.collector_source != "telethon":
        raise typer.BadParameter("COLLECTOR_SOURCE=web needs no login; set it to telethon first")
    Path(settings.telegram_session).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_telethon(settings).login_interactive())


@app.command()
def channels(config: Path = CHANNELS_YAML) -> None:
    """Resolve channels from the YAML and upsert them into the database."""
    from tgdigest.collector.config import load_channels_config
    from tgdigest.collector.sync import upsert_channels
    from tgdigest.db.base import make_engine, make_session_factory

    settings = get_settings()
    configure_logging(settings.log_level)
    cfg = load_channels_config(config)

    async def run() -> None:
        engine = make_engine(settings.database_url)
        try:
            async with _source(settings) as src:
                rows = await upsert_channels(make_session_factory(engine), src, cfg)
            for ch in rows:
                typer.echo(
                    f"{ch.topic:7s} @{ch.username:32s} {ch.title[:40]!r} subs={ch.subscribers}"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


@app.command()
def sync(
    topic: Annotated[str | None, typer.Option(help="scifi | humor | cinema")] = None,
    channel: Annotated[str | None, typer.Option(help="single @username")] = None,
    since: Annotated[
        datetime | None, typer.Option(help="history start, default 183 days ago")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="max messages per channel (debug)")] = None,
    media: bool = True,
) -> None:
    """Incrementally download new messages (and photos) for active channels."""
    from sqlalchemy import select

    from tgdigest.collector.media import MediaStore
    from tgdigest.collector.sync import link_forwards, sync_channel
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.db.models import Channel

    settings = get_settings()
    configure_logging(settings.log_level)
    since_utc = since.replace(tzinfo=UTC) if since and since.tzinfo is None else since

    async def run() -> None:
        engine = make_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                stmt = select(Channel).where(Channel.is_active).order_by(Channel.topic, Channel.id)
                if topic:
                    stmt = stmt.where(Channel.topic == topic)
                if channel:
                    stmt = stmt.where(Channel.username == channel.removeprefix("@"))
                targets = list((await session.execute(stmt)).scalars())
            if not targets:
                typer.echo("no matching active channels — run `collector channels` first")
                raise typer.Exit(1)
            store = MediaStore(settings.media_dir) if media else None
            async with _source(settings) as src:
                for ch in targets:  # sequential on purpose: one account, no parallelism
                    stats = await sync_channel(
                        factory, src, store, ch.id, since=since_utc, limit=limit
                    )
                    typer.echo(
                        f"@{stats.username:32s} fetched={stats.fetched:5d} "
                        f"inserted={stats.inserted:5d} "
                        f"media={stats.media_downloaded}+{stats.media_reused} "
                        f"floods={stats.flood_waits}"
                    )
            async with factory() as session:
                linked = await link_forwards(session)
            typer.echo(f"forwards linked to corpus channels: {linked}")
        finally:
            await engine.dispose()

    asyncio.run(run())


@app.command()
def refresh(
    topic: Annotated[str | None, typer.Option(help="scifi | humor | cinema")] = None,
    channel: Annotated[str | None, typer.Option(help="single @username")] = None,
    since: Annotated[
        datetime | None, typer.Option(help="history start, default 183 days ago")
    ] = None,
) -> None:
    """Re-read dates, texts and view/forward counters of stored posts from the source (no media)."""
    from sqlalchemy import select

    from tgdigest.collector.sync import refresh_channel
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.db.models import Channel

    settings = get_settings()
    configure_logging(settings.log_level)
    since_utc = since.replace(tzinfo=UTC) if since and since.tzinfo is None else since

    async def run() -> None:
        engine = make_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                stmt = select(Channel).where(Channel.is_active).order_by(Channel.topic, Channel.id)
                if topic:
                    stmt = stmt.where(Channel.topic == topic)
                if channel:
                    stmt = stmt.where(Channel.username == channel.removeprefix("@"))
                targets = list((await session.execute(stmt)).scalars())
            if not targets:
                typer.echo("no matching active channels — run `collector channels` first")
                raise typer.Exit(1)
            async with _source(settings) as src:
                for ch in targets:
                    stats = await refresh_channel(factory, src, ch.id, since=since_utc)
                    typer.echo(
                        f"@{stats.username:32s} fetched={stats.fetched:5d} "
                        f"dates_fixed={stats.dates_fixed:5d} texts_fixed={stats.texts_fixed:4d} "
                        f"enrichment_reset={stats.enrichment_reset:4d} "
                        f"counters={stats.counters_updated:5d} floods={stats.flood_waits}"
                    )
        finally:
            await engine.dispose()

    asyncio.run(run())


@app.command()
def stats() -> None:
    """Per-channel counts: posts, media, date range, sync cursor."""
    from tgdigest.collector.sync import channel_stats
    from tgdigest.db.base import make_engine, make_session_factory

    settings = get_settings()

    async def run() -> None:
        engine = make_engine(settings.database_url)
        try:
            async with make_session_factory(engine)() as session:
                rows = await channel_stats(session)
        finally:
            await engine.dispose()
        total = 0
        for r in rows:
            total += r["posts"]
            first = r["first"].date() if r["first"] else "-"
            last = r["last"].date() if r["last"] else "-"
            typer.echo(
                f"{r['topic']:7s} @{r['username']:32s} posts={r['posts']:6d} "
                f"media={r['with_media']:6d} {first}..{last} cursor={r['last_message_id']}"
            )
        typer.echo(f"total posts: {total}")

    asyncio.run(run())
