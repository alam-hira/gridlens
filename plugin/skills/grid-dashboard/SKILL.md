---
name: grid-dashboard
description: Use when the user asks for a GB electricity dashboard, a visual summary, or an HTML report of carbon intensity and the generation mix. Generates a self-contained dashboard with the GridLens engine; every number and chart is computed by the engine, not the model.
---

# grid-dashboard

Produces a **single, self-contained HTML dashboard** for the GB grid using the
GridLens engine (see `grid-calc`). The file inlines its own CSS and a locally
vendored Chart.js and bakes the data in at render time, so it opens standalone
with no network access and works on static hosting (e.g. GitHub Pages).

## When to use

- "Make me a dashboard / visual summary of the grid."
- "Generate an HTML carbon-intensity report for the last N days."

## How to run it

```bash
gridlens report --profile gb --days 7 --out gb.html
```

- `--out` sets the output path; `--days` the window (1–14); `--profile gb` is the
  national view.
- The command prints a one-line summary (mean intensity, renewable share, Layer B
  match rate). Point the user at the generated file to open in a browser.

## What the dashboard contains

- A freshness line (window covered, actual vs forecast), KPI tiles with
  week-over-week deltas and sparklines, the generation-mix doughnut, the
  half-hourly intensity trend (forecast segments dashed), the intensity-band
  breakdown, a period comparison, anomaly flags framed as observations, and a
  methodology + validation + attribution footer.
- Each chart canvas carries `role="img"`, an `aria-label`, and a text fallback, and
  a data-table view is included for non-visual reading.

## Presenting it

Summarise the headline numbers from the engine's printed summary and describe what
the dashboard shows; do not restate numbers you compute yourself. The footer
already carries the NESO CC BY 4.0 attribution and the modelling-choice notes —
mention that the figures are validated against the API's own statistics (Layer B)
and that the mix×factors reconstruction (Layer A) is indicative by design.
