"""``tgdigest prompts …`` — the versioned prompt files and their mirror in Langfuse (SPEC §7.2).

Files under ``prompts/`` stay the source of truth (reviewed in git, part of ``model_version``);
``push`` registers each ``<name>.v<N>.md`` in Langfuse as prompt ``<name>`` with a commit
message naming the file version and the label ``v<N>``, so traces can be linked to the exact
text that produced them and the Langfuse UI can diff versions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer

from tgdigest.config import get_settings
from tgdigest.prompts import PROMPTS_DIR

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Prompt files ↔ Langfuse")
_FILE = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+)\.md$")


def prompt_files(root: Path = PROMPTS_DIR) -> list[tuple[str, int, Path]]:
    out = []
    for path in sorted(root.glob("*.md")):
        m = _FILE.match(path.name)
        if m:
            out.append((m.group("name"), int(m.group("version")), path))
    return out


@app.command("list")
def list_prompts() -> None:
    """Prompt files and their versions."""
    for name, version, path in prompt_files():
        typer.echo(f"{name:<24} v{version}  {path.stat().st_size:6d} B")


@app.command()
def push(
    dry_run: Annotated[bool, typer.Option(help="print what would be registered")] = False,
) -> None:
    """Register every prompt file in Langfuse (idempotent: an identical text is not re-created)."""
    settings = get_settings()
    files = prompt_files()
    if dry_run:
        for name, version, _ in files:
            typer.echo(f"would push {name} (label v{version})")
        return
    if not settings.langfuse_public_key:
        typer.echo("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set")
        raise typer.Exit(1)
    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
    )
    pushed = skipped = 0
    for name, version, path in files:
        text = path.read_text(encoding="utf-8").strip()
        label = f"v{version}"
        try:
            existing = client.get_prompt(name, label=label, max_retries=0, fetch_timeout_seconds=5)
        except Exception:  # not registered yet (or unreachable: the create below will say)
            existing = None
        if existing is not None and existing.prompt == text:
            skipped += 1
            continue
        client.create_prompt(
            name=name,
            prompt=text,
            labels=[label, "production"],
            type="text",
            commit_message=f"{path.name} from git",
        )
        pushed += 1
        typer.echo(f"pushed {name} {label}")
    client.flush()
    typer.echo(f"pushed={pushed} unchanged={skipped}")
