# GridLens

*Trustworthy GB electricity analytics — deterministic engine, validated numbers, self-contained dashboard.*

[![CI](https://github.com/alam-hira/gridlens/actions/workflows/ci.yml/badge.svg)](https://github.com/alam-hira/gridlens/actions/workflows/ci.yml)

**Live dashboard:** activates here once the repo is made public and GitHub Pages is enabled — it will serve [`docs/index.html`](docs/index.html). Until then, open [`examples/gb.html`](examples/gb.html) locally.

GridLens turns the official GB **Carbon Intensity API** into a trustworthy
analytics agent: deterministic, typed Python computes every metric, the figures
are **validated against the API's own published statistics** to a tight tolerance
across every period, a FastAPI service exposes them as JSON, a Typer CLI operates
it, and it renders a **self-contained HTML dashboard** (Chart.js vendored
locally). It's a public demonstration of the techniques used to build a
production analytics system for a client under NDA — the same engineering
discipline, on open data, with all code written from scratch.

The rare, hireable signal at its centre: **trustworthy numbers**. Deterministic
code does the maths, every figure is reconciled against a known-good reference,
and the system fails loud instead of inventing values.

## The validation result (the crux)

Run against a live 7-day window, GridLens reconciles its own arithmetic to the
source and reports the gap honestly:

- **Layer B — exact reconciliation (tight):** the engine's recomputed
  mean/min/max intensity matches the API's own `/intensity/stats` for **7/7 daily
  windows within ±1 gCO₂/kWh** (largest mean gap 0.4 — pure integer rounding).
  This proves the arithmetic and date handling against a known-good reference.
- **Layer A — independent reconstruction (indicative):** rebuilding intensity
  from `mix × published factors` runs **~+29 gCO₂/kWh above** the official figure
  on average (std ~15), because the naive sum can't see embedded wind/solar,
  interconnector-imports-by-source, or T&D losses. GridLens **reports this gap
  and flags the outlier periods** rather than pretending to match.

Full report: [`examples/validation_report.md`](examples/validation_report.md) ·
sample dashboard: [`examples/gb.html`](examples/gb.html).

## Quickstart

### Windows (plain pip virtualenv)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# reproducible alternative: pip install -r requirements-lock.txt && pip install -e . --no-deps

ruff check .        # lint
mypy src            # type-check
pytest              # tests (53)

gridlens --help
gridlens report --profile gb --days 7 --out examples/gb.html   # build the dashboard
gridlens validate --profile gb --days 7                        # run both validation layers
gridlens serve                                                 # API at http://127.0.0.1:8000 (docs at /docs)
```

### macOS/Linux with [uv](https://github.com/astral-sh/uv)

```bash
uv sync --extra dev
make check                 # ruff + mypy + pytest
uv run gridlens serve
```

### Docker

```bash
docker build -t gridlens .
docker run -p 8000:8000 gridlens     # serves /health, /metrics, /docs
```

## Commands

| Command | What it does |
|---|---|
| `gridlens report --profile gb --days 7 --out gb.html` | Fetch, compute, and render the self-contained dashboard. |
| `gridlens validate --profile gb --days 7` | Run Layer B + Layer A and save the Markdown report. |
| `gridlens fetch --profile gb --days 7` | Fetch and cache the raw data (debugging). |
| `gridlens serve` | Launch the FastAPI service (`/metrics`, `/anomalies`, `/validation`, `/dashboard`, `/docs`). |

## How it works (one paragraph)

`client.py` fetches the API with timeouts/retries/caching; `models.py` validates
every response at the boundary (fail loud, never fabricate); `metrics.py` computes
the numbers deterministically with pandas; `validation.py` runs the two-layer
reconciliation; `anomalies.py` applies rule-based flags; `engine.py` orchestrates
fetch→compute into one `DashboardReport`; and the CLI, FastAPI service, and
Chart.js dashboard are thin surfaces over that single result — so the JSON and the
dashboard can never disagree. A full plain-English tour is in
[`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md); the design rationale is in
[`docs/gridlens-build-plan.md`](docs/gridlens-build-plan.md); the build write-up
is in [`docs/BUILD_REPORT.md`](docs/BUILD_REPORT.md).

## Config-driven regions

A region is a small validated YAML profile in `src/gridlens/profiles/` — adding
one needs no code change. `gb` is the national view; regional profiles
(e.g. Scotland) are **beta** and not yet wired into the engine's fetch path.

## Honest limitations

- **Layer A is indicative, not exact** — it omits imports-by-source, losses, and
  embedded generation; the gap is reported, not hidden.
- **Forecast vs actual** — recent/future half-hours may be forecast only; the
  dashboard labels which and counts the forecast-only periods.
- **Biomass-as-low-carbon** and the renewable definition are stated modelling
  choices (footer).
- **Charts render to `<canvas>`** — mitigated for accessibility with
  `role="img"`/`aria-label`/text fallback and a data-table view, but inherently
  less screen-reader-native than SVG.
- **The published dashboard is a snapshot**, not live, unless a scheduled refresh
  is added.

## Data & licence

Carbon intensity and generation data © National Energy System Operator (NESO),
via the Carbon Intensity API (carbonintensity.org.uk), used under CC BY 4.0. See
[`NOTICE`](NOTICE). Project code is MIT — see [`LICENSE`](LICENSE).
