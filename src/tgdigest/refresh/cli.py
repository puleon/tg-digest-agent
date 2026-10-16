"""``tgdigest refresh …`` — what the systemd unit runs, and a look at the last run."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Corpus refresh")


@app.command()
def run(
    trigger: Annotated[str, typer.Option(help="timer | cli (a pending bot request wins)")] = "cli",
) -> None:
    """Collect, enrich, dedup, index and rebuild profiles — one run at a time."""
    from tgdigest.refresh.pipeline import run_default
    from tgdigest.refresh.state import RefreshFiles

    settings = get_settings()
    configure_logging(settings.log_level)
    files = RefreshFiles(settings.data_dir)
    with files.hold_lock() as held:
        if not held:
            typer.echo("a refresh is already running")
            return
        request = files.take_request()
        requested_by = int(request["requested_by"]) if request else None
        status = asyncio.run(
            run_default(
                settings,
                files,
                trigger="bot" if request else trigger,
                requested_by=requested_by,
            )
        )
    typer.echo(f"{status.run_id}: {status.state}" + (f" — {status.error}" if status.error else ""))
    if status.state != "ok":
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """The last run, as the bot shows it (without the markup)."""
    import re

    from tgdigest.refresh.report import render
    from tgdigest.refresh.state import RefreshFiles

    files = RefreshFiles(get_settings().data_dir)
    text = render(files.read_status(), running=files.is_running())
    typer.echo(re.sub(r"</?[bi]>", "", text))
