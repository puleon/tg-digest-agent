"""``tgdigest agent …`` — ask the search agent from the terminal."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Search agent (M5)")


async def _run(
    query: str, *, user_id: int, tier: str, rewrite: bool, rerank: bool, threads: int | None
) -> dict[str, Any]:
    from qdrant_client import QdrantClient

    from tgdigest.agent.graph import AgentDeps, run_agent
    from tgdigest.agent.toolset import ToolDeps, build_registry, make_http
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.ingest.entities import DbCache, Grounder
    from tgdigest.llm.client import LLMClient
    from tgdigest.retrieval.embeddings import BGEM3Embedder
    from tgdigest.retrieval.index import PostIndex
    from tgdigest.retrieval.rerank import BGEReranker

    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    http = make_http(proxy=settings.web_proxy)

    async def confirm(tool: str, args: dict[str, Any]) -> bool:
        return typer.confirm(f"{tool} with {args} — apply?", default=False)

    try:
        deps = ToolDeps(
            index=PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=threads)),
            factory=factory,
            http=http,
            grounder=Grounder(client=http, cache=DbCache(factory)),
            reranker=BGEReranker(threads=threads) if rerank else None,
        )
        agent = AgentDeps(
            llm=LLMClient(settings),
            tools=build_registry(deps, confirmer=confirm),
            synthesis_tier=tier,  # type: ignore[arg-type]
            rewrite=rewrite,
        )
        state = await run_agent(agent, query, user_id=user_id)
    finally:
        await http.aclose()
        await engine.dispose()
    return dict(state)


@app.command()
def ask(
    query: str,
    user: Annotated[int, typer.Option(help="user id for the profile")] = 0,
    tier: Annotated[str, typer.Option(help="synthesis tier: fast | heavy")] = "fast",
    rewrite: Annotated[bool, typer.Option(help="LLM query rewriting")] = True,
    rerank: Annotated[bool, typer.Option(help="cross-encoder reranking of candidates")] = True,
    threads: Annotated[int | None, typer.Option(help="torch threads for the models")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print the whole state as JSON")] = False,
) -> None:
    """Answer a request over the corpus: route → retrieve → grade → synthesize."""
    settings = get_settings()
    configure_logging(settings.log_level)
    if tier not in ("fast", "heavy"):
        raise typer.BadParameter("tier must be fast or heavy")
    state = asyncio.run(
        _run(query, user_id=user, tier=tier, rewrite=rewrite, rerank=rerank, threads=threads)
    )
    if as_json:
        typer.echo(json.dumps(state, ensure_ascii=False, indent=1, default=str))
        return
    typer.echo(state.get("answer", ""))
    typer.echo("")
    usage = state.get("usage", {})
    typer.echo(
        f"— mode={state.get('mode')} topic={state.get('topic')} days={state.get('days')} "
        f"iterations={state.get('iteration')} tokens={usage.get('prompt_tokens', 0)}+"
        f"{usage.get('completion_tokens', 0)} degraded={state.get('degraded')}"
    )
    for s in state.get("steps", []):
        extra = {k: v for k, v in s.items() if k not in ("step", "seconds")}
        typer.echo(f"  {s['step']:<16} {s['seconds']:6.2f}s {extra}")
