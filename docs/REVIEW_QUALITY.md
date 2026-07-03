# GridLens — Adversarial Code-Quality Review

**Reviewer stance:** guilty until proven correct. Every checkable claim was checked
against the running code, not taken on trust.

**Date:** 2026-07-03 · **Scope:** `src/gridlens/**`, `tests/**`, packaging, and tooling,
measured against `docs/gridlens-build-plan.md`.

## What I ran (and the raw results)

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check .` | **All checks passed** |
| Format | `ruff format --check .` | **24 files already formatted** |
| Types (engine) | `mypy src` | **Clean, 13 files** (also clean with `--warn-unused-ignores`) |
| Types (engine+tests) | `mypy src tests` | **Errors** — but the error is a numpy-stub/`python_version` clash, not our code (see P2-4) |
| Tests | `pytest` | **44 passed** |
| Coverage | `pytest --cov` | **93%** total (see gap table under P1-3) |
| Wheel | `python -m build --wheel` + zip inspection | **Builds; package data present** (see "Verified sound") |
| CLI | `python -m gridlens.cli --help` | **Lists report/validate/fetch/serve** |

The repo is in genuinely good shape: green lint, green types on the engine, all tests
passing, and the packaging actually works. Most findings below are P2 polish. The three
P1s are a silently-disabled security control, a reproducibility gap, and an untested
core guarantee.

---

## P0 — must fix (correctness / security / packaging break)

**None found.** The categories most likely to hide a P0 were checked and are sound:

- **Packaging works** — I built the wheel and inspected it; templates, static Chart.js,
  profiles, and `py.typed` are all shipped (details below). `pip install gridlens` would
  render a working dashboard.
- **No injectable template break today** — autoescape *is* mis-configured (P1-1), but
  there is no current path for external/user input to reach an unescaped HTML sink, and
  the one script-context sink (`|tojson`) stays safe regardless. Verified empirically.
- **No arithmetic crash paths** — every division is guarded (`_delta`,
  `_intensity_deviation`, Layer A), every empty series returns an explicit "not
  available" rather than a fake 0, and every colour/label lookup uses `.get(..., default)`.

The single most important item is **P1-1**; treat it as borderline-P0 and fix it first.

---

## P1 — should fix (real quality / security / maintainability issue)

### P1-1. Jinja2 autoescape is silently **disabled** on the dashboard template
**File:** `src/gridlens/render.py:277` (the `select_autoescape(["html", "xml"])` call) ·
template `src/gridlens/templates/dashboard.html.j2`.

**What's wrong:** `select_autoescape(["html","xml"])` decides by the template's *final*
extension. The template is named `dashboard.html.j2`, which ends in `.j2`, not `.html`,
so the matcher returns its default of `False`. I verified this against the live library:

```
>>> select_autoescape(['html','xml'])('dashboard.html.j2')
False
>>> select_autoescape(['html','xml'])('dashboard.html')
True
```

So **every `{{ ... }}` in the template renders unescaped.** The author clearly *intended*
autoescape to be on: the template uses `|safe` deliberately on `chart_js` (line 315) and
on the methodology notes (line 292) — those `|safe` markers are meaningless unless
autoescape is otherwise escaping everything else. The mental model ("everything is escaped
except my explicit `|safe`") is currently violated for the whole page.

**Why it matters:** this is a defeated defense-in-depth control. Values that flow into
unescaped HTML text/attribute contexts include API-supplied fuel names
(`render.py:140`, rendered into the legend and into `aria-label`s), engine-built anomaly
messages (`{{ a.message }}`, template line 254), band names, and records. Today these come
from the trusted NESO API over HTTPS and profile titles come from bundled YAML, so it is
**not** externally exploitable — which is why this is P1, not P0. But the moment any
user-controlled string is rendered (a future regional label, a query param, a custom
profile) it becomes an XSS. (Note: the `<script>DATA=...</script>` block is *not* a
vector — `|tojson` escapes `<`/`>`/`&` to `\uXXXX` regardless of autoescape; I confirmed
`</script>` in a fuel label is neutralised.)

**Fix:** this Environment renders exactly one HTML template, so the simplest correct fix is
`autoescape=True`. If you prefer to keep `select_autoescape`, pass
`select_autoescape(default=True, default_for_string=True)` or add `"j2"` /
`"html.j2"` to the enabled set. Then re-run the render tests to confirm nothing that
*should* be HTML (the methodology `|safe` items) regressed.

### P1-2. No lockfile; runtime deps are lower-bound-only; Docker installs unpinned
**Files:** `pyproject.toml:13-24` (runtime) and `29-38` (dev), `Dockerfile:7`,
`.github/workflows/ci.yml`, `Makefile`.

**What's wrong:** the build plan's definition of done lists a **`uv.lock`** and sells
"reproducible (uv lockfile + Docker)" as a headline. There is no lockfile anywhere — no
`uv.lock`, no pinned `requirements*.txt`, nothing. Every runtime dependency is an open
lower bound (`pydantic>=2.6`, `fastapi>=0.110`, `pandas>=2.2`, …) with no upper cap. CI
runs `uv sync --extra dev` (ci.yml) and Docker runs `pip install .` (Dockerfile:7), both of
which resolve the dependency graph **fresh on every run**. A future breaking minor release
of any dep (pydantic and pandas both have form here) can turn CI red or change the built
image with no code change — the opposite of the reproducibility the project advertises.

**Why it matters:** it directly contradicts a stated DoD item and undermines the
"production-grade, reproducible" narrative the project is built to demonstrate.

**Fix:** commit a lockfile (`uv lock` → `uv.lock`, or `pip-compile` → a pinned
`requirements.txt`) and install from it in both CI and the Dockerfile
(`uv sync --frozen` / `pip install -r requirements.txt`). The dev/runtime *split* itself is
correct — httpx, types-stubs, and tooling are all correctly in `[project.optional-dependencies].dev`.

### P1-3. The HTTP client's fail-loud path has no test (45% coverage on `client.py`)
**File:** `src/gridlens/client.py:37-65` (uncovered: init/session/retry setup and the whole
`_get`).

**What's wrong:** the tests inject a `FakeClient` everywhere, so the real
`CarbonIntensityClient` — timeouts, retry/backoff config, `requests-cache`, and the
`except requests.RequestException → raise DataSourceError` translation — is **never
exercised**. Build-plan §14 step 5 makes "a forced error path raises clearly" an explicit
acceptance criterion, and "fail loud, never fabricate" is guiding principle #4; that exact
guarantee is the one line with zero coverage.

**Why it matters:** the headline promise of the project (loud failure on upstream trouble)
is asserted, not proven, precisely where it happens. A regression in that `except` clause
(e.g. someone swallows the error) would pass CI.

**Fix:** add a small unit test using `requests_mock`/`responses` (or by monkeypatching
`client.session.get` to raise / return a 500) that asserts `DataSourceError` is raised and
that `timeout=` is passed. This also lifts the weakest coverage number in the suite.

---

## P2 — optional / nice-to-have

### P2-1. Actual-else-forecast logic is triplicated
`metrics._effective` (`metrics.py:57`), `validation._effective_actual` (`validation.py:172`),
and `anomalies._actual` (`anomalies.py:41`) implement the same "prefer `actual`, else
`forecast`" rule three times. They agree today, but the fallback policy is a core semantic
that will drift if only one copy changes (`_effective` also returns a `used_forecast`
flag; the other two don't). Consolidate into one shared helper (e.g. in `models.py`) and
have the two flag-less callers use it. This is the duplication the brief asked about — it
is a real DRY smell, low-risk but worth removing.

### P2-2. `comparison()` docstring says "latest full day"; it isn't necessarily full
`metrics.py:199-217`. The docstring reads "the latest full day vs the day before," but the
code takes `days[-1]` — the latest calendar day *present* in the window, which for a report
ending at `now` is usually **today (partial)**. So a partial today is compared against a
complete yesterday, and the word "full" is inaccurate. Either reword the docstring or drop
the trailing partial day before comparing (`engine._window` already computes
`last_midnight`, which could gate it).

### P2-3. Factor-mapping comment claims "Other / Oil → other" but the code drops Oil
`validation.py:49` (comment) vs `validation.py:72-80` (`_DIRECT_FACTOR_NAMES`, only
`"other": "Other"`). The `Oil` factor (935 gCO₂/kWh in the fixture) is silently excluded
from the Layer A reconstruction, contradicting the comment. Since the coarse mix has a
single `other` bucket, either fold Oil into `other` the way `imports` averages
interconnectors, or fix the comment to say only `Other` is used. Right now the comment
lies about what the code does.

### P2-4. Tests are excluded from type-checking, and enabling it isn't turnkey
CI and the Makefile run `mypy src` only (`ci.yml`, `Makefile`), so the tests' type
annotations — including the `# type: ignore[arg-type]` at `tests/conftest.py:62` and
`tests/test_engine.py:36` — are never verified (those ignores could be stale and nobody
would know). Attempting `mypy src tests` in this environment fails with
`numpy/__init__.pyi:737: Type statement is only supported in Python 3.12 and greater`
— a clash between `python_version = "3.11"` (pyproject) and the installed numpy 2.5 stubs,
surfaced only once tests pull pandas/numpy into mypy's graph. Not a code defect, but it
means "just add tests to mypy" won't work as-is. If you want tests type-checked, add a
second mypy invocation scoped to `tests` with `follow_imports = skip` (or bump the analysis
`python_version`).

### P2-5. The `pre-commit` mypy hook is inconsistent with CI and likely errors locally
`.pre-commit-config.yaml` gives the mypy hook `additional_dependencies` of only pydantic,
pydantic-settings, and the two stub packages. The hook runs in its own isolated env with no
access to the project venv, and `fastapi`, `typer`, `jinja2`, and `pandas` are neither
provided there nor in the `ignore_missing_imports` override list in `pyproject.toml`. So
mypy-via-pre-commit on `api.py` (fastapi), `cli.py` (typer), and `render.py` (jinja2) will
report "cannot find implementation or library stub" even though CI's `mypy src` is green.
Fix: make the hook mirror CI — `pass_filenames: false`, `args: ["src"]`, and add the
missing deps (or the ignore overrides) to `additional_dependencies`. (Not runnable here to
confirm — no network for hook install — but it follows from how pre-commit isolates envs.)

### P2-6. Dead test fixture and its fixture file
`tests/conftest.py:75-79` defines the `intensity_date` fixture (and it is the only consumer
of `tests/fixtures/intensity_date.json`), but no test requests it. It looks like a
forecast-fallback test was planned and never written — either add that test (the engine's
forecast-fallback path in `metrics._effective` line 68 and `anomalies._actual` line 45 are
themselves uncovered, so it would earn its keep) or delete the fixture and JSON.

### P2-7. A new sqlite-backed `CachedSession` is created per client, and the cache lands in CWD
`client.py:38-50` builds a fresh `requests_cache.CachedSession` on every construction, and
`api.get_client` (`api.py:50-52`) constructs a new client **per request** via `Depends`, so
each HTTP request opens a new sqlite-backed session. Functionally fine, but it is needless
file-handle/connection churn under load; consider constructing the client once. Separately,
`cache_name="gridlens_cache"` is CWD-relative, so running the CLI drops a
`gridlens_cache.sqlite` wherever the user happens to be (it *is* git-ignored, so not a
commit risk). Consider an absolute path under a temp/cache dir.

### P2-8. `api.py` 502/500 exception mappings are untested
`api.py:114-126`. Only `ConfigError → 404` is covered (via the regional-profile test). The
`DataSourceError → 502`, `DataValidationError → 502`, and generic `GridLensError → 500`
handlers — an explicit §11 deliverable — are never hit (coverage confirms lines 116/121/126
missing). A test with a fake client that raises each error would close this.

### P2-9. Minor docstring / dead-value drift
- `metrics.records` (`metrics.py:160-169`) stores `best_renewable: tuple[date, float]` but
  only ever reads the `float`; the `date` element is dead (the datetime is tracked
  separately in `greenest_at`). Harmless, but a reader will wonder why the date is there.
- `render.py:10-24` — the module docstring narrates the *build environment* ("the
  frontend-design skill was not installed on this machine… Node is absent…"). It doesn't
  misdescribe behaviour, but build-time narrative rots in source; it belongs in
  `docs/BUILD_REPORT.md`.

### P2-10. README test-count drift and spec deviations (both honest, worth noting)
- `README.md:51` says "tests (41)"; the suite is now **44**. Cheap to keep current.
- Build-plan §7/§10 call for a **week-over-week** comparison and KPI change; the engine only
  implements **day-over-day** (`metrics.comparison`). This is a defensible scope cut and the
  template says "vs prev day" honestly, but it is a deviation from the plan; the README's
  "week-over-week" language should not creep back in.
- Build-plan §4 asks for explicit BST/GMT handling at day boundaries; the engine works
  entirely in UTC and groups by UTC date (`engine._window`, `metrics.daily_series`). The
  docstrings say "UTC date" honestly, so this is documented, not hidden — but GB "days" and
  UTC "days" diverge by an hour during BST, so the daily rollups are UTC-aligned, not
  local-day-aligned. Fine to keep; just be aware it is a simplification of the spec.

---

## Verified sound (where I looked and found no problem)

- **Packaging (built and inspected).** `python -m build --wheel` succeeds; the wheel
  contains `gridlens/profiles/gb.yaml` + `scotland.yaml`, `gridlens/templates/dashboard.html.j2`,
  `gridlens/static/chart.umd.js` (the real 205 KB Chart.js v4.4.6, not a stub),
  `gridlens/py.typed`, and `LICENSE`/`NOTICE` under `dist-info/licenses`. Hatchling's
  `packages = ["src/gridlens"]` (`pyproject.toml:40-41`) pulls the non-Python data in
  correctly because nothing under the package is git-ignored. `pip install gridlens` ships a
  working dashboard renderer.
- **Security basics.** No secrets/tokens/passwords anywhere in `src` (the API needs no
  auth). Config uses `yaml.safe_load` only (`config.py:63`); no `yaml.load`/`eval`/`exec`/
  `pickle`/`subprocess`. There is exactly one outbound call site (`client._get`,
  `client.py:56`) and it passes `timeout=self.settings.request_timeout`. The only two
  `|safe` uses (`dashboard.html.j2:292` methodology, `:315` vendored chart.js) are on
  trusted, non-external data. `|tojson` keeps the JS data block injection-safe even with
  autoescape mis-set (verified). (The autoescape mis-config itself is P1-1.)
- **Typing completeness of the engine.** `disallow_untyped_defs` + `disallow_incomplete_defs`
  + `no_implicit_optional` are on (`pyproject.toml:60-67`); `mypy src` is clean, and stays
  clean under `--warn-unused-ignores`. No `# type: ignore`, `# noqa`, `print(`, or
  TODO/FIXME anywhere in `src`. `Any` appears only where it should — raw payload dicts at the
  HTTP boundary and the render context dict — never leaking into a public metric signature.
- **Error handling.** Exceptions preserve their cause (`raise … from exc`) in `client._get`,
  `config.load_profile`, and every `models.parse_*`. Excepts are specific
  (`requests.RequestException`; `(KeyError, TypeError, ValidationError)`), never bare. The
  boundary parsers fail loud into `DataValidationError` rather than returning partial data.
- **Test quality.** Tests assert behaviour with hand-checked expected values
  (`test_metrics.py`, `test_validation.py`), include negative cases
  (`test_layer_b_flags_a_mismatch`, `*_quiet_when_*`), and check real dashboard properties
  (self-contained, no CDN, canvas accessibility, well-formedness). No tautological
  assertions and no fragile imports of private engine symbols from tests.

## Coverage gap breakdown (the missing ~7%)

| File | Miss | What it leaves untested | Matters? |
|---|---|---|---|
| `client.py` | 45% | entire real fetch: timeout, retry, cache, `DataSourceError` raise | **Yes → P1-3** (core fail-loud guarantee) |
| `api.py` | 90% | 502/500 exception handlers (116/121/126) | Minor → P2-8 (a §11 deliverable) |
| `cli.py` | 87% | `fetch` (88-90) and `serve` (99-101) commands, `main` | Minor; `serve` is hard to test, `fetch` is easy |
| `config.py` | 94% | invalid-YAML `except` branch (65-66) | Minor; the fail-loud config path |
| `models.py` | 95% | `parse_intensity`/`parse_stats` malformed-item branches | Minor; sibling parsers are covered |
| `validation.py` | 96% | forecast-fallback (176), empty-block/None guards | Negligible |
| `anomalies.py` | 96% | forecast-fallback (45), `baseline==0` guard (75) | Negligible |
| `metrics.py`/`render.py` | 98/97% | empty-input guards | Negligible |

---

## Fixes applied

Addressed in the fix session after this review (gate green throughout: ruff, `mypy src`, 53 tests, 96% coverage).

| ID | Finding | Outcome |
|---|---|---|
| P1-1 | Jinja2 autoescape silently off | **Fixed** — `render.py` now uses `autoescape=True`; render tests confirm `\|safe`/`\|tojson` still work and no escaped-tag leak. |
| P1-2 | No lockfile; unpinned deps | **Fixed** — added `requirements-lock.txt` (pip equivalent of uv.lock) + next-major caps in `pyproject.toml`; CI and Dockerfile now install the pinned set; Makefile switched to pip. |
| P1-3 | Client fail-loud path untested | **Fixed** — new `tests/test_client.py` forces `DataSourceError` and asserts the timeout is passed; `client.py` coverage 45% → 92%. |
| P2-1 | Actual-else-forecast logic triplicated | **Fixed** — single `models.effective_intensity`; metrics/validation/anomalies delegate to it. |
| P2-2 | `comparison()` docstring "latest full day" | **Fixed** — docstring corrected to "two most recent days present (latest may be partial)". |
| P2-3 | Factor-map comment claims Oil→other | **Fixed** — comment now states Oil and Pumped Storage are intentionally dropped. |
| P2-4 | Tests excluded from mypy | **Not fixed (documented)** — `mypy src tests` is blocked by numpy 3.x shipping 3.12-only stub syntax vs our 3.11 target; `follow_imports=skip` did not clear it. Gate stays `mypy src`; noted in `pyproject.toml`. |
| P2-5 | pre-commit mypy hook inconsistent with CI | **Fixed** — hook now `pass_filenames: false`, `args: ["src"]`, with fastapi/typer/jinja2 in `additional_dependencies`. |
| P2-6 | Dead `intensity_date` fixture | **Fixed** — turned into a real forecast-null dashboard test (`test_render.py`). |
| P2-7 | CachedSession per request / CWD cache | **Not changed** — functionally fine and the cache file is git-ignored; per-request client is required by the DI test seam. Acknowledged. |
| P2-8 | 502/500 handlers untested | **Fixed** — `test_api.py` now drives `DataSourceError → 502` and generic `GridLensError → 500`. |
| P2-9 | Dead date value in `records`; build-narrative docstring | **Fixed** — `records` tracks only the float + datetime; `render.py` docstring trimmed (provenance moved to BUILD_REPORT). |
| P2-10 | README test-count drift; spec deviations | **Fixed** — README/WALKTHROUGH now say 53 tests; day-over-day and UTC-day choices remain documented deviations. |
