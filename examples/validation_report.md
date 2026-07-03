# GridLens validation report — Great Britain electricity

*Generated 2026-07-03 16:27 UTC · window 2026-06-26T00:00Z to 2026-07-03T16:00Z*

## Layer B — exact aggregate reconciliation (tight)

- **Match rate:** 100% (7/7 windows) within ±1 gCO2/kWh
- **Largest mean difference:** 0.4 gCO2/kWh

| Day | Mean (ours) | Avg (stats) | Diff | Min o/s | Max o/s | Match |
|---|---|---|---|---|---|---|
| 2026-06-26 | 170.73 | 171 | -0.27 | 111/111 | 220/220 | ✓ |
| 2026-06-27 | 137.96 | 138 | -0.04 | 50/50 | 227/227 | ✓ |
| 2026-06-28 | 104.6 | 105 | -0.40 | 41/41 | 177/177 | ✓ |
| 2026-06-29 | 164.88 | 165 | -0.12 | 108/108 | 239/239 | ✓ |
| 2026-06-30 | 208.96 | 209 | -0.04 | 156/156 | 256/256 | ✓ |
| 2026-07-01 | 172.98 | 173 | -0.02 | 79/79 | 240/240 | ✓ |
| 2026-07-02 | 74.0 | 74 | +0.00 | 38/38 | 130/130 | ✓ |

## Layer A — independent reconstruction (indicative)

- **Periods compared:** 368
- **Mean difference (reconstructed − actual):** +29.46 gCO2/kWh
- **Mean absolute difference:** 29.46 gCO2/kWh
- **Spread (std dev):** 14.89 gCO2/kWh
- **Range:** -0.64 to +63.20 gCO2/kWh
- **Outliers flagged (>2σ from the mean gap):** 7

> Indicative only. Reconstructed as Σ(fuel_share × published_factor) using the coarse generation mix; it omits interconnector imports by source, transmission & distribution losses, and embedded (behind-meter) wind and solar, so a gap to the official figure is expected — the value is quantifying that gap, not matching it.

**Factor → mix mapping used (gCO2/kWh):** biomass=120, coal=937, gas=394, hydro=0, imports=328.33, nuclear=0, other=300, solar=0, wind=0

*Carbon intensity and generation data © National Energy System Operator (NESO), via the Carbon Intensity API (carbonintensity.org.uk), used under CC BY 4.0.*
