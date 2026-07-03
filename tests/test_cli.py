"""CLI tests via Typer's CliRunner, offline via a fake client (§12, §15)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gridlens.cli import app

from .conftest import FakeClient

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "report" in result.stdout
    assert "validate" in result.stdout
    assert "serve" in result.stdout


def test_report_writes_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gridlens.engine.CarbonIntensityClient", FakeClient)
    out = tmp_path / "gb.html"
    result = runner.invoke(app, ["report", "--days", "7", "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert out.exists()
    assert "Dashboard written" in result.stdout


def test_validate_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gridlens.engine.CarbonIntensityClient", FakeClient)
    out = tmp_path / "validation.md"
    result = runner.invoke(app, ["validate", "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert "Layer B" in out.read_text(encoding="utf-8")


def test_bad_profile_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gridlens.engine.CarbonIntensityClient", FakeClient)
    # Scotland is regional → the engine fails loud → the CLI exits non-zero.
    result = runner.invoke(app, ["report", "--profile", "scotland"])
    assert result.exit_code == 1
