"""Top-level CLI. Sub-commands for collector / ingest / agent are registered as modules land."""

from __future__ import annotations

import asyncio

import typer

from tgdigest import __version__
from tgdigest.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Tematic Telegram Index")

from tgdigest.agent.cli import app as agent_app  # noqa: E402
from tgdigest.collector.cli import app as collector_app  # noqa: E402
from tgdigest.dedup.cli import app as dedup_app  # noqa: E402
from tgdigest.ingest.cli import app as ingest_app  # noqa: E402
from tgdigest.profile.cli import app as profile_app  # noqa: E402
from tgdigest.prompts_cli import app as prompts_app  # noqa: E402
from tgdigest.retrieval.cli import app as index_app  # noqa: E402

app.add_typer(collector_app, name="collector")
app.add_typer(ingest_app, name="ingest")
app.add_typer(dedup_app, name="dedup")
app.add_typer(index_app, name="index")
app.add_typer(agent_app, name="agent")
app.add_typer(prompts_app, name="prompts")
app.add_typer(profile_app, name="profile")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def health() -> None:
    """Check that Postgres, Qdrant, the LLM server and Langfuse are reachable."""
    from tgdigest.health import check_all

    checks = asyncio.run(check_all(get_settings()))
    for c in checks:
        mark = "OK " if c.ok else "FAIL"
        typer.echo(f"[{mark}] {c.name:<10} {c.detail}")
    if not all(c.ok for c in checks):
        raise typer.Exit(code=1)
