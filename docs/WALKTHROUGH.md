# GridLens — a plain-English walkthrough

This document explains, in ordinary language, **how the whole project fits
together, what each part does, and how to run it**. Read it top-to-bottom and you
should be able to open any source file and know why it exists.

---

## 1. The big idea

GridLens answers "how is the GB electricity grid doing?" — carbon intensity, the
generation mix, whether anything is unusual — and it does so in a way you can
*trust*. The trust comes from three habits, applied everywhere:

1. **Code does the maths.** Every number is computed in deterministic, typed
   Python. Nothing is estimated by a language model.
2. **Reconcile to the source.** Where the data provider publishes its own
   aggregates, GridLens recomputes them independently and checks they match.
3. **Fail loud, never fabricate.** If a value can't be computed, the code raises a
   clear error or shows a dash — it never guesses.

Everything else — the CLI, the API, the dashboard, the tests — is built around
those three habits.

## 2. The shape of the system

There is **one engine** and several **thin surfaces** over it:

```
        Carbon Intensity API (NESO, public, no auth)
                     │
             client.py  ── fetch (timeouts, retries, cache)
                     │
             models.py  ── validate every response at the boundary
                     │
   ┌─────────────────┼───────────────────────────┐
 metrics.py      validation.py                anomalies.py
 (the numbers)   (Layer B + Layer A)          (rule-based flags)
   └─────────────────┼───────────────────────────┘
                 engine.py  ── fetch + compute → one DashboardReport
                     │
   ┌─────────────────┼─────────────────┐
 cli.py           api.py            render.py
 (Typer)         (FastAPI)        (HTML dashboard)
```

The key move is `engine.py`: it is the single place that turns "profile + number
of days" into a fully-computed `DashboardReport`. The CLI, the API, and the
dashboard renderer all call it, so **the numbers on the dashboard and the numbers
in the JSON come from the same computation** and can never drift apart.

## 3. Module by module (the engine)

### `exceptions.py` — the fail-loud foundation
A small hierarchy: `GridLensError` is the base, with `ConfigError`,
`DataSourceError`, `DataValidationError`, and `MetricError` underneath. Every
failure mode has a named type so callers can handle it explicitly (and the API
can map each to the right HTTP status).

### `logging_config.py` — no `print` in a library
A tiny helper to get module-scoped loggers. Libraries log; they don't print, so
the host application controls verbosity.

### `client.py` — the data-access layer
A thin, resilient wrapper over the API. It sets explicit **timeouts** (so it never
hangs), **retries with backoff** on transient 5xx/429 errors, and **caches**
responses to SQLite (via `requests-cache`) so re-runs don't hammer the source.
Every method (`intensity`, `generation`, `stats`, `factors`) returns already-parsed,
validated models — or raises `DataSourceError`. It never returns a half-fetched
response as if it were complete.

### `models.py` — typed schema + boundary parsing
Two families of Pydantic models:
- **API response shapes** (`IntensityPeriod`, `GenerationPeriod`, `StatsPeriod`,
  and the factors parser) mirror the JSON the API returns. The `from`/`to`/`max`/
  `min` wire names (reserved words) are aliased to safe attribute names.
- **Computed result shapes** (`IntensityMetrics`, `MixMetrics`, `MetricsReport`,
  etc.) are what the engine *produces* — plain typed containers that serialise
  straight to JSON and feed the dashboard.

The `parse_*` functions are the **boundary**: they turn a raw `dict` into models
and raise `DataValidationError` on anything unexpected. This is where "fail loud"
is enforced — a malformed payload dies here, not three layers downstream as a
silent wrong number. (`_rows` also smooths over an API quirk: the current-snapshot
endpoints return one object under `data` while the range endpoints return a list;
both are normalised to a list.)

### `config.py` — settings and region profiles
`Settings` holds runtime knobs (base URL, timeout, cache TTL, retry count, anomaly
thresholds), overridable via `GRIDLENS_*` environment variables — the standard
12-factor approach. `Profile` is a validated YAML "view" of the grid (national or
regional). **Adding a region is adding a YAML file, not writing code** — that's the
"generalisable, not a one-off" property, and it's tested.

### `metrics.py` — the calculation engine (all the maths)
Pure functions, each taking validated models in and returning a typed result:
- `intensity_metrics` — mean/min/max, the cleanest and dirtiest half-hours, the
  spread across index bands. It uses **"actual where present, else forecast"** and
  records how many periods fell back, so the dashboard can label it honestly.
- `mix_metrics` — the window-average share per fuel (a pandas group-by-mean), plus
  the renewable / low-carbon / fossil splits.
- `records`, `comparison` (day-over-day deltas), `trend` (the half-hourly series),
  and `daily_series` (per-day rollups behind the sparklines).
- `build_metrics_report` assembles them into one `MetricsReport`.

The fuel classification lives here as documented constants: **renewable** = wind +
solar + hydro; **low-carbon** additionally counts nuclear and **biomass** (a stated,
debatable modelling choice); **fossil** = gas + coal; imports and other are kept
separate because their true carbon content isn't knowable from the coarse mix.

### `validation.py` — the two-layer reconciliation (the crux)
This is the heart of the "trustworthy" claim.

- **Layer B — exact reconciliation (tight).** Recompute the window's mean/min/max
  from the half-hourly series and check it against the API's own
  `/intensity/stats` for the same day. Because both come from the same series, the
  only legitimate difference is that the stats endpoint rounds the average to an
  integer — so the tolerance is **±1 gCO₂/kWh**, and the result reports the **match
  rate across every day**, not one lucky example. (Empirically, `stats.min`/`.max`
  equal our min/max exactly and `stats.average` equals `round(our mean)`.)

- **Layer A — independent reconstruction (indicative).** Rebuild intensity from
  `Σ(fuel_share × published_factor)` and compare to the reported intensity per
  half-hour. This deliberately **won't** match, because the official methodology
  adds things the coarse mix can't see. Layer A **quantifies the gap** (mean bias,
  spread, range) and **flags the outlier periods**, rather than claiming success.

  The tricky bit is the **factor → fuel mapping** (see §4).

`validate()` runs both and bundles them into a `ValidationReport`.

### `anomalies.py` — deterministic, rule-based flags
Three simple rules, thresholds from config, each flag carrying the numbers behind
it and framed as *an observation to verify*:
1. **Intensity deviation** — latest half-hour vs the norm for the *same time of
   day* (so the normal daily cycle isn't flagged).
2. **Record period** — the latest reading sets a window low/high, or a renewable
   high.
3. **Fuel-share swing** — a fuel's latest share jumps far from its window average.

### `engine.py` — the orchestration seam
`build_report(profile, days)` computes the fetch window (aligned so Layer B only
ever reconciles complete days), fetches the four inputs, runs metrics + anomalies
+ validation, and returns one `DashboardReport`. Everything is **injectable**
(`client`, `settings`, `now`) so the whole pipeline runs offline against fixtures
in tests. It also renders the Markdown validation report. Regional profiles fail
loud here (regional analytics are beta) rather than silently returning national
numbers under a regional label.

## 4. The one real design decision: the factor → fuel mapping

The published factors use **granular** names (`Gas (Combined Cycle)`, `Gas (Open
Cycle)`, `Dutch Imports`, `French Imports`, `Irish Imports`, …). The generation
mix uses **coarse** names (`gas`, `imports`, …). To reconstruct intensity you must
collapse one onto the other, and two fuels are genuinely ambiguous:

- **`gas`** → we use the **Combined-Cycle** factor (394), because CCGT supplies the
  bulk of GB gas while Open-Cycle is a small peaking reserve the coarse mix can't
  distinguish. Trade-off: during peaking events this understates gas intensity.
- **`imports`** → we use the **mean of all interconnector factors** (Dutch/French/
  Irish ≈ 328), because the mix reports one aggregate imports share with no source
  split. Trade-off: the real value swings with the live flow mix (France is
  nuclear-heavy and very low), which we can't see — this is the **single biggest
  reason Layer A is only indicative**, and we report it rather than hide it.

Both choices are documented in `validation.py`, surfaced in the dashboard's
methodology footer, and could be promoted into profile config later.

## 5. The surfaces

- **`cli.py` (Typer)** — `report`, `validate`, `fetch`, `serve`. Each is a thin
  wrapper over `build_report`; engine errors become a clean message and a non-zero
  exit code, never a stack trace.
- **`api.py` (FastAPI)** — `/health`, `/metrics`, `/anomalies`, `/validation`,
  `/dashboard`, plus auto docs at `/docs`. The HTTP client is a dependency so tests
  override it with a fake. Engine exceptions map to HTTP status codes
  (bad/unavailable profile → 404, upstream failure or bad shape → 502, other → 500;
  bad query params → 422 automatically).
- **`render.py` + `templates/dashboard.html.j2` + `static/chart.umd.js`** — turns a
  `DashboardReport` into **one self-contained HTML file**: all CSS inline, Chart.js
  inlined (no CDN, no sibling files, works offline), data baked in. It has KPI
  tiles with week-over-week deltas and sparklines, a mix doughnut, the intensity
  trend (forecast segments dashed), an intensity-band bar, a period-comparison
  table, anomaly cards, and a methodology + validation + attribution footer. Every
  canvas carries `role="img"`, an `aria-label`, and text fallback, with a
  data-table view for non-visual reading. It's theme-aware (light/dark) with a
  toggle.

## 6. How the data flows (one request)

```
gridlens report --days 7
  → engine.build_report("gb", 7)
      → client.intensity / generation / stats / factors   (fetch + validate)
      → metrics.build_metrics_report(...)                 (the numbers)
      → anomalies.detect(...)                             (the flags)
      → validation.validate(...)                          (Layer B + Layer A)
      → DashboardReport
  → render.build_dashboard(report)                        (one HTML file)
  → written to examples/gb.html
```

## 7. Testing

53 tests, none of which touch the network — they run against **captured fixtures**
in `tests/fixtures/`. Coverage spans: metric maths (hand-checked expected values),
fail-loud parsing, the add-a-region config test, Layer B (100% reconciliation on
the fixtures) and Layer A (a real difference distribution), every anomaly rule
firing *and* staying quiet, the full engine pipeline, the dashboard
(self-contained + accessible + well-formed), the real HTTP client's fail-loud path,
the CLI (Typer's `CliRunner`), and the API (FastAPI's `TestClient` with the client
dependency overridden). Overall coverage is 96%.

## 8. How to run everything (Windows, pip)

```powershell
# from the repo root, with the venv active (.venv\Scripts\Activate.ps1)
pip install -e ".[dev]"

# quality gate
ruff check .
mypy src
pytest

# build the dashboard from live data, then open it
gridlens report --profile gb --days 7 --out examples/gb.html
start examples\gb.html

# see the validation numbers
gridlens validate --profile gb --days 7

# run the API and explore the auto docs
gridlens serve
#   then browse http://127.0.0.1:8000/docs
```

If a fresh shell doesn't have the venv active, call the executables by path, e.g.
`.\.venv\Scripts\gridlens.exe report --days 7`.
