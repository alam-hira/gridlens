---
name: grid-monitor
description: Use when the user asks how the GB electricity grid is doing today or over a window — the carbon intensity, the generation mix, the low-carbon/renewable share, or whether anything is unusual. Runs the GridLens engine via its CLI; the engine, not the model, produces every number.
---

# grid-monitor

Answers "how is the grid doing?" for Great Britain using the GridLens engine. All
numbers come from the engine (see `grid-calc`) — never estimate them yourself.

## When to use

- "How clean is the grid right now / today / this week?"
- "What's the generation mix?" / "How much is renewable?"
- "Is anything unusual on the grid?"

## How to run it

Compute a report for the window the user asked about (default 7 days):

```bash
gridlens report --profile gb --days 7 --out gb.html
```

For machine-readable numbers instead of a dashboard, use the API:

```bash
gridlens serve            # then, in another shell:
curl "http://127.0.0.1:8000/metrics?profile=gb&days=7"
curl "http://127.0.0.1:8000/anomalies?profile=gb&days=7"
```

Map the user's phrasing to `--days`: "today" → `--days 1`, "this week" → `--days 7`.

## How to present the results

Read these straight from the engine output and report them plainly:

- **Average carbon intensity** (gCO₂/kWh) and its index band (low → very high).
- **Renewable share** (wind + solar + hydro) and **low-carbon share** (adds nuclear
  and biomass — note that biomass-as-low-carbon is a stated modelling choice).
- **Cleanest and dirtiest half-hour** in the window, with their times.
- **Anomaly flags**, if any — present each as an *observation to verify*, quoting
  the numbers the engine attached (e.g. "latest intensity is 32% above the 7-day
  norm for this time of day"). If none fired, say the grid looks normal.

Always mention the window covered and whether any half-hours were forecast-only.
Attribute the data to NESO (CC BY 4.0). If the engine reports a value as
unavailable, say so — do not substitute a guess.
