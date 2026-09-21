"""``tgdigest digest …`` — build and show digest issues."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Digest crew (M7)")


@app.command()
def make(
    user: int = 0,
    minutes: Annotated[int, typer.Option(help="reading budget → 5–12 items")] = 10,
    days: Annotated[int, typer.Option(help="candidate window")] = 7,
    tier: Annotated[str, typer.Option(help="editor/critic tier: fast | heavy")] = "fast",
    threads: int | None = None,
    store: Annotated[bool, typer.Option(help="save the issue (digests table)")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan → curate → edit → critique one issue for the user and print it."""
    from qdrant_client import QdrantClient

    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.digest.runner import as_json as to_json
    from tgdigest.digest.runner import make_digest, render_text
    from tgdigest.llm.client import LLMClient
    from tgdigest.retrieval.embeddings import BGEM3Embedder
    from tgdigest.retrieval.index import PostIndex

    settings = get_settings()
    configure_logging(settings.log_level)
    if tier not in ("fast", "heavy"):
        raise typer.BadParameter("tier must be fast or heavy")

    async def go() -> tuple[Any, int | None]:
        engine = make_engine(settings.database_url)
        try:
            index = PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=threads))
            return await make_digest(
                make_session_factory(engine),
                index,
                LLMClient(settings),
                user,
                minutes=minutes,
                window_days=days,
                tier=tier,
                store=store,
            )
        finally:
            await engine.dispose()

    result, digest_id = asyncio.run(go())
    if as_json:
        typer.echo(json.dumps(to_json(result, digest_id), ensure_ascii=False, indent=1))
        return
    typer.echo(render_text(result))
    typer.echo(
        f"\n— digest #{digest_id} · items={len(result.items)} · critic_iterations="
        f"{result.critic_iterations} · problems={len(result.critic_problems)} · "
        f"tokens={result.usage.prompt_tokens}+{result.usage.completion_tokens} · "
        f"{result.seconds}s · degraded={result.degraded}"
    )


@app.command()
def show(digest_id: int) -> None:
    """Print a stored issue."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.db.models import Digest

    settings = get_settings()

    async def go() -> Any:
        engine = make_engine(settings.database_url)
        try:
            async with make_session_factory(engine)() as session:
                return await session.get(Digest, digest_id)
        finally:
            await engine.dispose()

    row = asyncio.run(go())
    if row is None:
        typer.echo(f"no digest {digest_id}")
        raise typer.Exit(1)
    typer.echo(
        f"digest #{row.id} · user {row.user_id} · {row.created_at:%Y-%m-%d %H:%M} · "
        f"critic_iterations={row.critic_iterations} · plan={row.plan_json}"
    )
    for i, it in enumerate(row.items_json, 1):
        typer.echo(
            f"{i}. {it.get('title')} [{it.get('topic')} · @{it.get('channel')}] — {it.get('why')}"
        )
