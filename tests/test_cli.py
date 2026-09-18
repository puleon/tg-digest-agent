from __future__ import annotations

from typer.testing import CliRunner

from tgdigest import __version__
from tgdigest.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
