# GridLens — Build Report

*A detailed, plain-English record of what was built, the decisions made, the
validation numbers, and how to run it. Companion to
[`WALKTHROUGH.md`](WALKTHROUGH.md) (how it fits together) and
[`gridlens-build-plan.md`](gridlens-build-plan.md) (the original spec).*

---

## 1. What was built, module by module

The starting point was a verified scaffold (package layout, tooling, and the
plumbing modules `exceptions`, `logging_config`, `models`, `config`, `client`,
plus CLI/API shells and stub modules). This build filled in the engine logic,
wired the surfaces, produced the dashboard, and wrote the docs.

| File | Role | What it now does |
|---|---|---|
| `models.py` | Typed schema + boundary parsing | Added `parse_factors` and the full set of **computed result models** (`IntensityMetrics`, `MixMetrics`, `Records`, `Delta`, `TrendPoint`, `DailyPoint`, `MetricsReport`). Parsers now normalise the API's single-object-vs-list quirk and fail loud on bad shapes. |
| `metrics.py` | The maths (§7) | Implemented `intensity_metrics`, `mix_metrics` (pandas group-by-mean), `records`, `comparison` (day-over-day), `trend`, `daily_series`, and `build_metrics_report`. Fuel classification constants live here. |
| `validation.py` | Two-layer reconciliation (§8) | Implemented **Layer B** (`reconcile_stats`), **Layer A** (`reconstruct_intensity`), the **factor→fuel mapping** (`map_factors_to_mix`), and `validate()` that bundles both. |
| `anomalies.py` | Rule-based flags (§9) | Implemented the three deterministic rules (intensity deviation, record period, fuel-share swing), each carrying its numbers. |
| `engine.py` | **New** orchestration seam | `build_report(profile, days)` → one `DashboardReport`; `validation_markdown()` for the saved report. Injectable client/settings/now for offline testing. |
| `render.py` | Dashboard (§10) | `build_dashboard`/`write_dashboard` — a single self-contained HTML file via Jinja2 + inlined Chart.js. Semantic palette, accessibility, methodology footer. |
| `cli.py` | Typer CLI (§12) | `report`, `validate`, `fetch` wired to the engine; clean error→exit-code handling. `serve` launches the API. |
| `api.py` | FastAPI (§11) | `/metrics`, `/anomalies`, `/validation`, `/dashboard` return real results; exception→HTTP-status mapping; client injected via a dependency. |
| `templates/dashboard.html.j2` | Dashboard template | Full theme-aware, accessible dashboard layout. |
| `static/chart.umd.js` | Vendored Chart.js 4.4.6 (MIT) | Inlined into every generated dashboard — no CDN, no network. |
| 4 × `SKILL.md` | Claude plugin skills | `grid-calc` (shared engine), `grid-monitor`, `grid-trends`, `grid-dashboard` — triggering descriptions and "the engine, not the model, produces every number". |

**How they connect:** `client` fetches → `models` validates → `metrics` /
`validation` / `anomalies` compute → `engine` bundles into one `DashboardReport`
→ `cli` / `api` / `render` are thin surfaces over that single object. Because all
three surfaces call the same `build_report`, the dashboard and the JSON can never
disagree.

### Fixtures captured this session
From the live API (no auth): `intensity_range.json` (7 days, 337 half-hours, all
`actual` present), `stats_range.json` (7 daily statistics blocks), and
`generation_range.json` (7 days of the coarse mix). These let the tests exercise
Layer B, Layer A, and the whole engine **offline**.

## 2. Design decisions I made (with reasoning and trade-offs)

The plan left a few genuine choices open. Each was resolved with a sensible
default, documented in code and in the dashboard footer.

### 2.1 The factor → fuel mapping (Layer A — the headline decision)
The published factors are **granular** (`Gas (Combined Cycle)` 394, `Gas (Open
Cycle)` 651, `Dutch Imports` 474, `French Imports` 53, `Irish Imports` 458, …);
the generation mix is **coarse** (`gas`, `imports`, …). Collapsing one onto the
other forces two decisions:

- **`gas` → Combined-Cycle factor (394).** CCGT supplies the overwhelming majority
  of GB gas generation; OCGT is a small peaking reserve the coarse mix can't
  reveal. *Trade-off:* understates gas intensity during peaking events. (Alternative
  considered: averaging CC and OC to 522 — rejected because CCGT so dominates that
  CC-only is closer to reality most of the time.)
- **`imports` → mean of the interconnector factors (≈328).** The mix reports one
  aggregate imports share with **no source split**. *Trade-off:* the true value
  swings with live flows (France is nuclear-heavy and very low; others higher),
  which we can't see — this is the **single biggest reason Layer A diverges**, and we
  report it rather than hide it. (Alternative considered: a France-weighted value —
  rejected as unknowable without live interconnector data; the neutral mean is the
  honest choice.)

Both live in `validation.py` with full comments, are surfaced in the dashboard's
methodology footer and the validation report, and could later be promoted into
profile config.

### 2.2 A dedicated `engine.py` orchestration module
The plan's module list didn't include one, but the CLI, API, and renderer all need
the same "fetch a window, compute everything" path. Rather than duplicate it three
times, I added a thin `engine.py`. *Trade-off:* one module beyond the spec, but it
keeps the surfaces DRY and guarantees a single source of truth for every number.

### 2.3 Layer B reconciles against the `actual` series, per-day
I verified empirically that the `/intensity/stats` blocks are computed from the
`actual` series: `stats.min`/`.max` equal the series min/max exactly, and
`stats.average` equals `round(mean(actual))`. So Layer B recomputes from
"actual, else forecast" and tests **every complete day** (via `stats/.../24`),
reporting a match rate rather than one example.

### 2.4 Window alignment
The report window is `[midnight `days` ago, now]` (so today's data shows,
including any forecast tail), but Layer B only reconciles the **complete days** up
to today's midnight — so a partial current day never breaks the tight tolerance.

### 2.5 Inlining Chart.js (stronger than "reference by relative path")
The plan allows vendoring Chart.js and referencing it by relative path. I inline
it into the generated HTML instead, so the output is **one portable file** with no
companions — it opens standalone and needs no sibling `static/` folder. The
vendored `chart.umd.js` remains committed as the source of truth.

### 2.6 Semantic fuel palette + accessibility
Fuels get **meaning-carrying** colours (wind=blue, solar=yellow, gas=orange,
nuclear=violet, …) drawn from the `dataviz` skill's pre-validated categorical
slots. Because there are 9 fuels (above the 8-slot categorical guidance), colour is
never the sole channel: every segment carries a text label, a legend, and a
data-table view (the skill's "relief rule"). The intensity ramp runs green→red.
*Note:* the `frontend-design` skill named in the plan isn't installed on this
machine, so the `dataviz` skill was used; and Node being absent, the palette
validator script couldn't be run — the pre-validated hexes plus labels stand in.

### 2.7 Biomass counted as low-carbon; renewable = wind+solar+hydro
A stated modelling choice (biomass lifecycle emissions are debated). Renewable is
the strict wind+solar+hydro; low-carbon additionally counts nuclear and biomass.
Both are stated in the footer rather than hidden.

### 2.8 Regional profiles fail loud
`gb` (national) is fully wired. Regional profiles are beta (§17): `build_report`
raises `ConfigError` for them rather than returning national numbers under a
regional label. The config mechanism itself (add-a-region-with-no-code) is proven
by the test suite and the bundled `scotland.yaml`.

## 3. Validation results (actual numbers)

From a live 7-day run (`gridlens validate --days 7`, saved to
[`examples/validation_report.md`](../examples/validation_report.md)):

### Layer B — exact aggregate reconciliation (tight)
- **Match rate: 100% — 7/7 daily windows** within **±1 gCO₂/kWh**.
- **Largest mean difference: 0.4 gCO₂/kWh** (pure integer rounding).
- Every day's recomputed **min and max equal the API's exactly**; the mean equals
  `round(stats.average)` in all seven.

| Day | Mean (ours) | Avg (stats) | Diff | Min o/s | Max o/s |
|---|---|---|---|---|---|
| 2026-06-26 | 170.73 | 171 | −0.27 | 111/111 | 220/220 |
| 2026-06-28 | 104.60 | 105 | −0.40 | 41/41 | 177/177 |
| 2026-07-02 | 73.90 | 74 | −0.10 | 38/38 | 130/130 |

*(three of seven shown; all seven matched.)*

### Layer A — independent reconstruction (indicative, honest)
Reconstructing `Σ(fuel_share × mapped_factor)` and comparing to the reported
intensity per half-hour, over **368 periods** (latest live run):

- **Mean difference (reconstructed − actual): +29.46 gCO₂/kWh** — a systematic
  positive bias. The naive sum runs *higher* than the official figure, mainly
  because it can't credit **embedded (behind-meter) wind & solar** (which pull the
  official number down) and because the flat imports proxy overstates during
  France-heavy periods.
- **Mean absolute difference: 29.46** · **spread (std): 14.89** · **7 outlier
  periods** flagged (>2σ from the mean gap) — clustered in high-renewable /
  high-import windows where the reconstruction overshoots most.

This is exactly the intended outcome: Layer A demonstrates the engine computes
**independently**, then quantifies the gap and surfaces the interesting periods,
instead of pretending to match. (Exact figures move slightly with each live run;
the committed [`examples/validation_report.md`](../examples/validation_report.md)
holds the current table.)

*(The offline test fixtures give the same shape — mean gap ≈ +27, std ≈ 14 — so the
behaviour is stable, not cherry-picked.)*

### Factor → mix mapping used (gCO₂/kWh)
`biomass 120 · coal 937 · gas 394 (CCGT) · hydro 0 · imports 328.33 (mean of
interconnectors) · nuclear 0 · other 300 · solar 0 · wind 0`

## 4. Final quality-gate results

Run with the repo's venv on Windows (Python 3.14 in this environment; the package
targets ≥3.11):

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check .` | **All checks passed!** |
| Format | `ruff format --check .` | **25 files already formatted** |
| Types | `mypy src` | **Success: no issues found in 13 source files** |
| Tests | `pytest` | **53 passed** |
| Coverage | `pytest --cov=gridlens` | **96%** overall |

Per-module coverage: `engine` 100%, `metrics` 98%, `validation` 96%, `anomalies`
96%, `render` 97%, `config` 94%, `models` 95%, `api` 94%, `client` **92%** (was
45% before the review added the fail-loud/timeout tests), `cli` 87%. Well above the
plan's ≥85% engine bar.

**Test breakdown (53):** models/fail-loud (incl. factors parser + single-object
form), config/add-a-region, metrics (hand-checked, empty-mix guard), validation
(Layer B 100% + Layer A distribution + mismatch detection), all three anomaly
rules (fire, stay quiet, and no spurious record on flat/single windows), the full
engine pipeline (+ window-clamp regression), the dashboard (self-contained,
accessible, well-formed, forecast-labelled), the real HTTP client's fail-loud path
and timeout, the CLI (`CliRunner`), and the API (`TestClient` — 200/404/422/502/500).
None hit the network.

### Post-review hardening

After the initial build, two independent adversarial subagent reviews were run
(code-quality and logic/behaviour — see [`REVIEW_QUALITY.md`](REVIEW_QUALITY.md)
and [`REVIEW_LOGIC.md`](REVIEW_LOGIC.md), each with a "Fixes applied" section).
Neither found a P0. The four P1s and the worthwhile P2s were fixed:

- **Jinja2 autoescape was silently off** (a `.j2` suffix defeated
  `select_autoescape`) → now `autoescape=True`; the render tests confirm `|safe`
  and `|tojson` still work and no escaped-tag leaks.
- **The report window wasn't clamped** → a boundary half-hour became a spurious
  single-sample "day" and shifted the window start back 30 min. Now clamped to
  `[window_start, end)`; a 7-day report yields exactly 7 daily points.
- **The client's fail-loud path had no test** → added a real
  `DataSourceError`/timeout test (client coverage 45% → 92%).
- **No lockfile / uncapped deps** → added `requirements-lock.txt` (the pip
  equivalent of a uv.lock) and next-major caps in `pyproject.toml`; CI and the
  Dockerfile now install the pinned set.
- Plus: consolidated the triplicated actual-else-forecast helper, guarded a
  spurious record-flag on flat/single windows and a bare `KeyError` on an empty
  mix, corrected drifted docstrings/comments, and added the missing 502/500 and
  forecast-null tests. One P2 (type-checking `tests/` too) is documented as
  blocked by a numpy-stub / Python-version clash.

## 5. How to run everything on Windows

```powershell
# from the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# quality gate
ruff check .
mypy src
pytest

# build + open the dashboard (live data)
gridlens report --profile gb --days 7 --out examples/gb.html
start examples\gb.html

# validation numbers
gridlens validate --profile gb --days 7

# the API + auto docs
gridlens serve
#   browse http://127.0.0.1:8000/docs  and  /metrics?profile=gb&days=7
```

If a shell doesn't have the venv active, call executables by path, e.g.
`.\.venv\Scripts\gridlens.exe report --days 7`. Docker also works:
`docker build -t gridlens . && docker run -p 8000:8000 gridlens`.

## 6. Known limitations & future work

**Limitations (also in the README):**
- Layer A is indicative — omits imports-by-source, T&D losses, embedded generation.
- Recent/future half-hours may be forecast-only; the dashboard labels and counts them.
- Regional analytics are beta and not wired into the engine's fetch path.
- Charts use `<canvas>` — mitigated with `role`/`aria`/fallback + a table view.
- The published dashboard is a snapshot unless a scheduled refresh is added.

**Natural next steps:**
- Promote the factor→fuel mapping into profile config (per §5 "config-driven").
- Wire the regional endpoints so regional profiles compute a real (beta) result.
- Deploy the Dockerised API to a free tier; add a scheduled Action to refresh the
  Pages dashboard daily (snapshot → living page).
- Optional Octopus Agile price layer for a "cheapest & greenest window" feature.
- Run the `dataviz` palette validator (needs Node) as a CI step.

## 7. Explain it in an interview (60 seconds)

> **GridLens is an open-source GB electricity analytics agent that proves its
> numbers are trustworthy.** A typed, tested Python engine pulls the official
> Carbon Intensity API, validates every response at the boundary with Pydantic,
> and computes carbon-intensity and generation-mix metrics deterministically with
> pandas — the model never does the maths. The centrepiece is a **two-layer
> validation**: Layer B recomputes the mean/min/max and reconciles to the API's
> own published statistics — **100% of daily windows within ±1 gCO₂/kWh** — which
> proves the arithmetic against a known-good reference; Layer A reconstructs
> intensity from mix × published factors, which is only *indicative* and lands
> **~29 gCO₂/kWh** off, so instead of faking a match I **report the gap and flag
> the outliers**. The engine is operated by a Typer CLI, served by a FastAPI
> service with auto OpenAPI docs, Dockerised, and packaged as a Claude plugin, and
> it renders a **self-contained, accessible dashboard** with Chart.js vendored
> locally. The one real modelling decision — mapping the API's granular fuel
> factors onto the coarse mix, especially using CCGT for gas and an
> interconnector-mean for imports — is documented in the code and the dashboard
> footer, with its trade-offs stated. It's the public twin of an NDA client
> system: same engineering discipline, open data, all code from scratch.
