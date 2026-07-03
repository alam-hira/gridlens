"""Typer command-line interface — how the tool is operated (build-plan §12).

Each command is a thin wrapper over the engine: it parses options, calls
``engine.build_report`` (the one fetch-and-compute path), and presents the
result. Errors from the engine's exception hierarchy are turned into a clear
message and a non-zero exit code, never a stack trace dump.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer

from .engine import build_report, validation_markdown
from .exceptions import GridLensError
from .logging_config import configure_logging
from .render import write_dashboard

app = typer.Typer(
    help="GridLens - trustworthy GB electricity analytics.",
    no_args_is_help=True,
    add_completion=False,
)

# Shared options, defined once so every command documents them identically.
ProfileOpt = typer.Option("gb", "--profile", "-p", help="Region profile to use (e.g. gb).")
DaysOpt = typer.Option(7, "--days", "-d", min=1, max=14, help="Window length in days.")


@app.command()
def report(
    profile: str = ProfileOpt,
    days: int = DaysOpt,
    out: Path = typer.Option(
        Path("examples/gb.html"), "--out", "-o", help="Where to write the dashboard HTML."
    ),
) -> None:
    """Fetch, compute, and render the self-contained dashboard."""
    configure_logging()
    result = _run(lambda: build_report(profile, days))
    written = write_dashboard(result, out)
    intensity = result.metrics.intensity
    typer.echo(f"Dashboard written to {written}")
    typer.echo(
        f"  {result.title}: mean {intensity.mean} gCO2/kWh over {result.metrics.n_periods} "
        f"half-hours; renewable {result.metrics.mix.renewable_share}%; "
        f"Layer B {_pct(result.validation.layer_b.match_rate)} match."
    )


@app.command()
def validate(
    profile: str = ProfileOpt,
    days: int = DaysOpt,
    out: Path = typer.Option(
        Path("examples/validation_report.md"),
        "--out",
        "-o",
        help="Where to write the Markdown validation report.",
    ),
) -> None:
    """Run both validation layers, print a summary, and save the report."""
    configure_logging()
    result = _run(lambda: build_report(profile, days))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(validation_markdown(result), encoding="utf-8")

    layer_b = result.validation.layer_b
    layer_a = result.validation.layer_a
    typer.echo(f"Validation report written to {out}")
    typer.echo(
        f"  Layer B: {_pct(layer_b.match_rate)} of {layer_b.windows_tested} windows "
        f"reconcile within +/-{layer_b.tolerance_gco2:g} gCO2/kWh."
    )
    typer.echo(
        f"  Layer A: mean gap {layer_a.mean_difference} gCO2/kWh "
        f"(abs {layer_a.mean_abs_difference}, std {layer_a.std_difference}); "
        f"{len(layer_a.outliers)} outliers flagged (indicative only)."
    )


@app.command()
def fetch(profile: str = ProfileOpt, days: int = DaysOpt) -> None:
    """Fetch and cache the raw data for a window (for debugging)."""
    configure_logging()
    result = _run(lambda: build_report(profile, days))
    typer.echo(
        f"Fetched {result.metrics.n_periods} half-hours for {result.title} "
        f"({result.window_from} to {result.window_to}). Cache warmed."
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the FastAPI service (interactive docs at /docs)."""
    import uvicorn

    uvicorn.run("gridlens.api:app", host=host, port=port)


_T = TypeVar("_T")


def _run(action: Callable[[], _T]) -> _T:
    """Execute an engine call, converting GridLensError into a clean CLI failure."""
    try:
        return action()
    except GridLensError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
