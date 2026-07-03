---
name: grid-trends
description: Use when the user asks how the GB grid compares over time — today vs yesterday, this week vs last, or a metric's trend across a window (carbon intensity or a fuel's share). Runs the GridLens engine via its CLI; the engine, not the model, computes every delta.
---

# grid-trends

Answers "how has the grid changed?" using the GridLens engine's deterministic
period-over-period comparisons. Never compute a delta or percentage in the model —
take them from the engine (see `grid-calc`).

## When to use

- "Is the grid cleaner than yesterday / last week?"
- "How has renewable / gas share moved recently?"
- "Show me the carbon-intensity trend over the last N days."

## How to run it

```bash
gridlens report --profile gb --days 7 --out gb.html
# or JSON:
gridlens serve   # then GET /metrics?profile=gb&days=7
```

The metrics object carries:

- `comparison` — day-over-day deltas (latest full day vs the day before) for mean
  intensity and the renewable / low-carbon / fossil shares, each with the absolute
  and percentage change.
- `daily` — per-day mean/min/max intensity and renewable/low-carbon share (the
  series behind the sparklines and any trend narrative).
- `trend` — the half-hourly intensity series across the whole window.

## How to present the results

- State the direction and size of each change from the engine's `comparison`
  (e.g. "mean intensity fell 18 gCO₂/kWh, −11%, vs the previous day").
- For carbon intensity, **down is cleaner**; for renewable/low-carbon share, **up is
  greener** — say which way is "good" so the direction is unambiguous.
- If the window has fewer than two days, the engine returns no comparison — say a
  comparison isn't available rather than inventing one.

Attribute the data to NESO (CC BY 4.0).
