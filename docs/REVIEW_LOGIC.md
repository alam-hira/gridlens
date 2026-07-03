# GridLens — Logic & Behaviour Review

**Reviewer stance:** adversarial ("guilty until proven correct"). Every claim below was
verified by *running the code* against the fixtures and by *recomputing metrics by hand*,
not by trusting the tests. Scratch scripts constructed the inputs and called the engine
functions directly.

**Environment:** `./.venv/Scripts/python.exe`, fixtures in `tests/fixtures/`, offline
`FakeClient` from `tests/conftest.py`, `now = 2026-07-03T00:00Z`.

## Verdict

The engine is **substantially correct and honest**. All fail-loud paths raise the right
named errors; no path silently returns a fabricated zero/empty; all awkward-but-valid
inputs are handled; every metric I independently recomputed matched the engine exactly;
all API status codes and CLI exit codes are correct.

- **P0 (must fix): none found.**
- **P1 (should fix): 1** — the report window is not clamped, so a boundary period from the
  previous day leaks into the daily/sparkline series and shifts the reported window start.
- **P2 (optional): 3** — record-flag fires on ties/flat/single-period windows; a raw
  `KeyError` crash on an (unlikely) empty generation mix; dashboard rounds the mean where
  the JSON does not.

Tests: 44/44 pass.

---

## P1 — Report window is not clamped: a 7-day report yields 8 "days"

**Files:** `src/gridlens/engine.py:110-111` (fetch), `src/gridlens/metrics.py:245-285`
(`daily_series`), `src/gridlens/metrics.py:294-295` (`window_from/window_to`).

**What's wrong:** `build_report` requests `intensity` over `[window_start, end]` but the
engine then computes over *whatever periods the client returns*, with no clamping to the
requested window. The `intensity_range.json` / `generation_range.json` fixtures both begin
with the boundary settlement period `2026-06-25T23:30Z → 2026-06-26T00:00Z` (this mirrors
the real Carbon Intensity API, whose `/intensity/{from}/{to}` returns the period ending at
`from`). Because `daily_series` groups by UTC calendar day, that single leading half-hour
becomes its own "day".

**Concrete trigger (verified):**
```
build_report("gb", days=7, client=FakeClient(), now=2026-07-03T00:00Z)
```
- `len(metrics.daily) == 8` for a **7-day** window.
- First daily point: `day=2026-06-25, mean=185.0, min=185, max=185` — a whole "day"
  synthesised from ONE half-hour.
- `report.window_from == 2026-06-25 23:30:00+00:00` (dashboard header renders
  "Window 25 Jun 2026 23:30 → …"), not the intended `2026-06-26T00:00Z`.

**Expected vs actual:** a 7-day report should present 7 daily points and a window starting
at 06-26T00:00. Actual: 8 points (one a single-sample sliver) and a window starting 30 min
early. Layer B is unaffected (it only reconciles complete `/intensity/stats` day-blocks, so
the stray period is dropped), and the window-average intensity is only perturbed by 1 period
in 337 (negligible) — so no headline number is *wrong*, but the daily series / sparklines and
the displayed window are visibly off.

**Suggested fix:** clamp fetched periods to `[window_start, end)` in `build_report` (drop
periods whose `start < window_start`), or have `daily_series` ignore days whose period count
is below a threshold (e.g. `< 2`). Clamping in the engine is cleaner and also fixes
`window_from`.

---

## P2 — Record-period flag fires on ties, flat windows, and single periods

**File:** `src/gridlens/anomalies.py:95-135` (intensity record) and `:137-153` (renewable
record).

**What's wrong:** the intensity record rule flags a "record low"/"record high" whenever the
latest observed value **equals** the window min/max (`observed == min(values)`), with no
guard for `len == 1` and no distinction between *setting* a new extreme and merely *tying*
one. On a flat or single-period window this fires spuriously.

**Concrete triggers (verified):**
- Single period `actual=123` → `detect(...)` returns
  `record_period: "Latest half-hour is the cleanest in the window … a record low."`
- Three identical periods (`actual=100` each) → two flags fire: a "record low" and (with a
  flat 50/50 mix) a renewable "window high".

Note this is only cosmetic on realistic data: on the shipped 7-day fixture the latest value
(83) is neither the window min (38) nor max (256), so **no** false record fires there, and a
realistic varied "normal" window correctly returns `[]` (verified). The word "record" also
slightly overstates a tie.

**Expected vs actual:** a flat/degenerate window is unremarkable; emitting "record low" on a
tie is noise. **Suggested fix:** require `len(values) > 1` for the intensity record (matching
the `len(shares) > 1` guard the renewable branch already has), and/or require a *strict*
extreme that is uniquely held by the latest period.

---

## P2 — `mix_metrics` raises a raw `KeyError` on an all-empty generation mix

**File:** `src/gridlens/metrics.py:105-117`.

**What's wrong:** `mix_metrics` guards `if not periods` but not "periods present, but every
`generationmix` is empty". In that case the DataFrame comprehension yields no rows, so
`pd.DataFrame(...)` has no `fuel` column and `frame.groupby("fuel")` raises
`KeyError: 'fuel'` — an unhandled crash rather than the module's contracted named error.

**Concrete trigger (verified):**
```
mix_metrics([GenerationPeriod(from=…, to=…, generationmix=[])])  →  KeyError: 'fuel'
```

**Expected vs actual:** the parser accepts `generationmix: []` as structurally valid, so a
downstream consumer should degrade gracefully (empty `MixMetrics`) or fail loud with a
`DataValidationError`, not raise a bare `KeyError`. Low likelihood — the real API always
returns nine fuels — hence P2. **Suggested fix:** early-return `MixMetrics()` if the exploded
frame is empty, or assert non-empty at the parse boundary.

---

## P2 — Dashboard rounds the mean where `/metrics` JSON does not (single-source-of-truth)

**Files:** `src/gridlens/render.py:170` (`round(intensity.mean)`), template
`templates/dashboard.html.j2:140`, `:202-203`.

**What's wrong:** `/metrics` serves `intensity.mean = 147.82`; the dashboard KPI and the
trend chart's aria-label show `148`. Every *other* headline number matches the JSON exactly
(renewable 40.05, fossil 32.14, min 38, max 256, all deltas — verified present verbatim in
the HTML). This is the only place the page and the JSON differ, and it is a deliberate,
defensible display rounding (whole gCO₂/kWh is conventional) — but the plan's "single source
of truth" claim (§10) is, strictly, one integer off here.

**Expected vs actual:** JSON `147.82` vs dashboard `148`. **Suggested fix:** either display
`147.8` / `147.82` to match, or document that KPI intensities are rounded to whole
gCO₂/kWh for display. No correctness impact.

---

## Verified CORRECT (checked, so the fix session need not re-verify)

### A. Fail-loud (all raise the right named error; none returns a silent zero/empty)
- Missing `data` key, missing `intensity` object, `generationmix` not a list, stats missing
  `max`, non-numeric factor value, empty factor rows, non-numeric `perc` → **all
  `DataValidationError`** (`models.py:190-245`).
- Client pointed at a dead URL (`http://127.0.0.1:59999`, `max_retries=0`) → **`DataSourceError`**
  (`client.py:64-65`).
- Unknown profile → **`ConfigError`** (`config.py:60-61`); regional profile (`scotland`) →
  **`ConfigError`** in `build_report` (`engine.py:99-103`).

### B. No over-failing (awkward-but-valid inputs work)
- **Forecast-only period** (`actual=null`): `intensity_metrics`/`trend` use the forecast and
  set `n_forecast_used` (`metrics.py:57-68`). Verified on `intensity_date.json`
  (n_forecast_used=21/48).
- **Single-period window:** mean/min/max, comparison (returns `[]`, correct), `daily_series`,
  `mix_metrics`, `anomalies.detect`, Layer A (`std=0.0` via the `len>1` guard,
  `validation.py:288`), Layer B — all run without error.
- **Mix not summing to 100** (e.g. 30+30): handled — each fuel averaged independently.
- **Unknown fuel** (`fusion`): included in `shares`/`ranked`, excluded from
  renewable/low-carbon/fossil totals, rendered with a default grey (`render.py:142-143`) —
  dashboard does **not** crash and does not silently drop it.
- **Missing factor for a mapped fuel** (no `Gas (Combined Cycle)`): `map_factors_to_mix`
  omits gas; `reconstruct_intensity` sums only `fuel in mapping` (`validation.py:273-277`) —
  no crash, fuel excluded (not treated as 0-intensity incorrectly).
- **Normal grid** (varied, latest mid-range) → `detect()` returns `[]` cleanly.
- **All-null intensity / empty window** → `mean=None` and `n_periods=0`, never a fabricated 0
  (`metrics.py:75-77`, `intensity_metrics`/`build_metrics_report`).

### C. Maths (independently recomputed by hand; all match)
- Window intensity: **mean 147.82, min 38, max 256**; cleanest 2026-07-02T13:30 (38),
  dirtiest 2026-06-30T02:30 (256) — match.
- Index distribution `{high:0.273, moderate:0.4065, low:0.2255, very high:0.095}` (sums to
  1.0) — match; it is a **fraction 0..1**, correctly ×100 only in the band bar
  (`render.py:151`), no double-scaling.
- Mix window averages (e.g. gas 32.14, wind 26.3, solar 12.09) and derived
  **renewable 40.05 / low-carbon 54.56 / fossil 32.14** — match.
- Day-over-day intensity delta 07-01→07-02: cur 73.9, prev 172.98, abs −99.08, **−57.28%** —
  match.
- **Timezone/day boundary:** `...Z` parses to timezone-aware UTC (`utcoffset 0:00:00`);
  `23:30Z` groups to 06-25, `00:00Z` groups to 06-30 — correct UTC calendar-day rollups, no
  naive/aware mixing in `reconcile_stats` comparisons.
- **Division guards:** previous share = 0 → `percent=None` (`metrics.py:177`); baseline = 0 in
  anomaly deviation → rule skipped (`anomalies.py:74-75`); Layer A std = 0 → no outliers
  (`validation.py:293,302`); absent fuel → `shares.get(fuel, 0.0)` (`metrics.py:127`). All hold.
- **Layer B:** `matched` requires mean **and** min **and** max within ±1 (`validation.py:209-213`);
  the round-then-compare on the mean is the correct like-for-like against the API's integer
  average. On the fixture, `stats.average == round(mean(actual))` and min/max are exact for all
  7 days (100% match, max mean gap 0.4). A synthetic block off by 5 on min/max → `matched=False`;
  off by 10 on the mean → `matched=False`. Confirmed.
- **Layer A mapping:** `gas = Gas (Combined Cycle) = 394` (not Open Cycle 651);
  `imports = mean(474, 53, 458) = 328.33`; unmapped fuel excluded (`validation.py:83-103`).
  First period recon 186.22 vs actual 185 (Δ +1.22) recomputed by hand — match.

### D. Contracts
- **API status codes** (FastAPI `TestClient`, `get_client` overridden): healthy 200; bad
  profile → **404**; regional profile (`ConfigError`) → **404**; `days=99`/`days=0` → **422**;
  `DataSourceError` → **502**; `DataValidationError` → **502**; `MetricError`/other
  `GridLensError` → **500**. All correct; Starlette resolves the most-derived handler so the
  `GridLensError` catch-all never shadows the specific ones (`api.py:109-126`).
- **CLI exit codes:** `validate --profile scotland` and `report --profile bogus` both print a
  clean `Error: …` and exit **1** (`cli.py:107-113`), no traceback.
- **Forecast-only dashboard** (built over `intensity_date.json`): `n_forecast_used=21`
  surfaced in the header ("(21 forecast-only)"); 21 trend points carry `is_forecast=True`; the
  JS `trendForecast` array feeds the dashed-segment logic; "Solid = actual · dashed = forecast"
  legend present.
- **Single source of truth:** all headline numbers except the rounded mean (see P2 above) appear
  verbatim in the HTML; the renderer performs no recomputation, only formatting/colour.
- **Single-object endpoint form** (`generation.json`, object-under-`data`) parses via `_rows`
  normalisation (`models.py:190-202`).

---

## Fixes applied

Addressed in the fix session after this review (gate green throughout: ruff, `mypy src`, 53 tests, 96% coverage).

| Finding | Outcome |
|---|---|
| **P1 — report window not clamped (8 "days" for a 7-day report)** | **Fixed** — `engine.build_report` now clamps both series to `[window_start, end)`. A 7-day report yields exactly 7 daily points and `window_from == window_start` (regression test `test_window_is_clamped_to_requested_days`). Sample-report counts updated 337 → 336 across tests. |
| **P2 — record flag fires on ties/flat/single-period windows** | **Fixed** — `anomalies._record_period` now requires `len(values) > 1` **and** `min != max` before flagging; tests `test_record_not_flagged_on_single_period` / `_on_flat_window` added. |
| **P2 — `mix_metrics` raw `KeyError` on empty mix** | **Fixed** — early-returns an explicit empty `MixMetrics()` when the exploded frame is empty; test `test_mix_metrics_empty_mix_is_explicit` added. |
| **P2 — dashboard rounds mean where JSON does not** | **No change (intentional)** — the KPI shows whole gCO₂/kWh by display convention; the exact value is served by `/metrics` and appears in the data table. Documented rather than changed. |

All items the review verified as **correct** (fail-loud paths, hand-recomputed maths, timezone/day-boundary handling, division guards, Layer B tolerance logic, Layer A mapping, API status codes, CLI exit codes, forecast labelling) were left unchanged and continue to pass.
