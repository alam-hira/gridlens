---
name: grid-calc
description: Shared GridLens engine for GB electricity data. Use when another skill needs carbon-intensity or generation-mix metrics, or a validation report, computed from the official Carbon Intensity API. Runs the installed `gridlens` CLI/API; the deterministic engine — not the model — produces every number.
---

# grid-calc

The shared engine the other GridLens skills build on. It wraps the official GB
**Carbon Intensity API** (NESO, CC BY 4.0, no auth) in a typed, tested Python
package that computes every metric deterministically and validates it against the
API's own published statistics.

## The one rule

**Never compute or estimate numbers in the model.** Carbon intensity, shares,
averages, deltas, and validation figures come *only* from the `gridlens` engine.
If the engine cannot produce a value, report the gap — do not fill it in.

## Setup

The skill assumes the `gridlens` package is installed and on PATH:

```bash
pip install gridlens        # or: pip install -e .  from a repo checkout
gridlens --help
```

## How to invoke the engine

| Need | Command |
|---|---|
| Full computed report + dashboard | `gridlens report --profile gb --days 7 --out gb.html` |
| Two-layer validation report | `gridlens validate --profile gb --days 7 --out validation.md` |
| Warm the cache / debug a fetch | `gridlens fetch --profile gb --days 7` |
| JSON for programmatic use | `gridlens serve` then `GET /metrics?profile=gb&days=7` |

`--profile` selects a region view (`gb` is national; regional profiles are beta).
`--days` is the window length (1–14).

The JSON `/metrics`, `/anomalies`, and `/validation` endpoints return the exact
same computed objects the dashboard is rendered from, so numbers never disagree
between surfaces.

## What the engine guarantees

- **Deterministic maths** — all arithmetic and date handling in typed Python.
- **Validated to the source** — Layer B reconciles the recomputed mean/min/max to
  `/intensity/stats` within ±1 gCO₂/kWh across every day in the window.
- **Fail loud** — missing data or an unexpected schema raises a clear error rather
  than producing a plausible wrong value.
