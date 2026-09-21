"""``tgdigest profile …`` — onboarding labels, profile build, ranked candidates."""

from __future__ import annotations

import asyncio
import csv
import html
import shutil
from pathlib import Path
from typing import Annotated, Any

import typer

from tgdigest.config import get_settings
from tgdigest.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Profile & interest (M6)")

STYLE = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:20px auto;padding:0 12px}
.post{display:grid;grid-template-columns:280px 1fr;gap:12px;border:1px solid #ddd;
      padding:8px;margin:10px 0}
img{max-width:100%;max-height:300px}pre{white-space:pre-wrap;font-size:13px;margin:0}
small{color:#666}h2{border-top:3px solid #333;padding-top:10px;margin-top:26px}
"""


def _index(threads: int | None = None):  # type: ignore[no-untyped-def]  # heavy imports
    from qdrant_client import QdrantClient

    from tgdigest.retrieval.embeddings import BGEM3Embedder
    from tgdigest.retrieval.index import PostIndex

    settings = get_settings()
    return PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder(threads=threads))


@app.command()
def onboard(
    out: Annotated[Path, typer.Option(help="folder for labels.csv + sheet.html")] = Path(
        "data/eval/onboarding"
    ),
    per_topic: int = 12,
    seed: int = 0,
) -> None:
    """Draw the cold-start sample (SPEC §6.6) and write a rating sheet: like / dislike / skip."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.profile.runner import onboarding_sample

    settings = get_settings()
    configure_logging(settings.log_level)

    async def go() -> list[Any]:
        engine = make_engine(settings.database_url)
        try:
            async with make_session_factory(engine)() as session:
                return await onboarding_sample(session, per_topic=per_topic, seed=seed)
        finally:
            await engine.dispose()

    posts = asyncio.run(go())
    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    parts = [
        f"<title>Onboarding</title><style>{STYLE}</style>",
        "<h1>Would you want this in your digest?</h1>",
        "<p>Rate each post in <code>labels.csv</code>: <b>like</b> = yes, I'd read/see this; "
        "<b>dislike</b> = no; <b>skip</b> = can't say. Rate by taste, not by quality.</p>",
    ]
    topic = None
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["post_id", "topic", "channel", "signal"])
        for p in posts:
            w.writerow([p.post_id, p.topic, p.channel, ""])
            if p.topic != topic:
                topic = p.topic
                parts.append(f"<h2>{topic}</h2>")
            img = ""
            if p.media_path and (settings.media_dir / p.media_path).exists():
                src = settings.media_dir / p.media_path
                dst = out / "img" / f"{p.post_id}{src.suffix}"
                if not dst.exists():
                    shutil.copyfile(src, dst)
                img = f'<img src="img/{dst.name}"><br>'
            parts.append(
                f'<div class="post"><div>{img}<small>post {p.post_id} · @{p.channel} · '
                f"{p.posted_at:%Y-%m-%d}</small></div>"
                f"<pre>{html.escape(p.text[:900])}</pre></div>"
            )
    (out / "sheet.html").write_text("\n".join(parts), encoding="utf-8")
    typer.echo(f"{len(posts)} posts -> {out}/labels.csv, sheet.html")


@app.command("import")
def import_labels(
    labels: Annotated[Path, typer.Option(help="labels.csv from onboard")] = Path(
        "data/eval/onboarding/labels.csv"
    ),
    user: int = 0,
    context: str = "onboarding",
) -> None:
    """Record the ratings as feedback rows for the user."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.profile.runner import record_feedback

    settings = get_settings()
    votes = [
        (int(r["post_id"]), r["signal"].strip().lower())
        for r in csv.DictReader(labels.open(encoding="utf-8"))
        if r["signal"].strip()
    ]

    async def go() -> int:
        engine = make_engine(settings.database_url)
        try:
            return await record_feedback(make_session_factory(engine), user, votes, context=context)
        finally:
            await engine.dispose()

    typer.echo(f"recorded {asyncio.run(go())} votes for user {user}")


@app.command()
def build(user: int = 0, threads: int | None = None) -> None:
    """Build the profile from the user's feedback and store it."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.profile.runner import build_and_store_profile

    settings = get_settings()
    configure_logging(settings.log_level)

    async def go() -> Any:
        engine = make_engine(settings.database_url)
        try:
            return await build_and_store_profile(
                make_session_factory(engine), _index(threads), user
            )
        finally:
            await engine.dispose()

    p = asyncio.run(go())
    typer.echo(
        f"votes={p.votes} like_rate={p.like_rate} topics={p.topic_weights} "
        f"channels={len(p.channel_affinity)} centroids={len(p.interest_centroids)} "
        f"negative={p.negative_prefs}"
    )


@app.command()
def top(
    user: int = 0,
    days: int = 7,
    limit: int = 15,
    baseline: Annotated[
        bool, typer.Option(help="rank by views instead (the §6.6 baseline)")
    ] = False,
    threads: int | None = None,
) -> None:
    """Ranked candidates for the user from the last ``days`` days."""
    from tgdigest.db.base import make_engine, make_session_factory
    from tgdigest.profile.runner import rank_candidates

    settings = get_settings()

    async def go() -> list[dict[str, Any]]:
        engine = make_engine(settings.database_url)
        try:
            return await rank_candidates(
                make_session_factory(engine),
                _index(threads),
                user,
                days=days,
                limit=limit,
                baseline=baseline,
            )
        finally:
            await engine.dispose()

    for i, row in enumerate(asyncio.run(go()), 1):
        s = row["score"]
        parts = " ".join(f"{k[:3]}={v:.2f}" for k, v in s.items() if k != "total")
        typer.echo(
            f"{i:2d} {s['total']:6.3f} post {row['post_id']:6d} @{row['channel']:<20} "
            f"{row['topic']:<6} views={row['views'] or 0:<7} {parts}  {row['text'][:60]!r}"
        )
