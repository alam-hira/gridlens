"""Rule-based anomaly flags (build-plan §9).

Simple, deterministic rules — no ML, no model judgement. Each flag is framed as
an *observation to verify* and carries the numbers behind it, so a reader can
check the claim rather than trust it. Thresholds are parameters (wired to config
in §5), so tuning a rule never touches this logic.

The three rules:

1. **Intensity deviation** — the latest observed half-hour is more than
   ``deviation_pct`` away from the recent average for the *same time of day*
   (a like-for-like baseline, so we don't flag the normal daily cycle).
2. **Record period** — the latest half-hour sets a window low or high for
   intensity, or the latest mix sets a window high for renewable share.
3. **Fuel-share swing** — a fuel's latest share moved more than ``swing_pp``
   percentage points from its recent window average.
"""

from __future__ import annotations

from datetime import time
from statistics import mean

from pydantic import BaseModel, Field

from .metrics import _renewable_share
from .models import GenerationPeriod, IntensityPeriod, IntensityValue, effective_intensity


class Anomaly(BaseModel):
    """One flagged observation, with the numbers that triggered it."""

    rule: str
    severity: str  # "info" (notable) or "watch" (worth a look)
    message: str
    observed: float
    baseline: float | None = None
    detail: dict[str, float] = Field(default_factory=dict)


def _actual(value: IntensityValue) -> int | None:
    """Observed intensity: actual, else forecast (so future-only periods count)."""
    return effective_intensity(value)


def _latest_observed(periods: list[IntensityPeriod]) -> IntensityPeriod | None:
    """The most recent period that carries a real ``actual`` reading."""
    for period in reversed(periods):
        if period.intensity.actual is not None:
            return period
    return None


def _intensity_deviation(
    periods: list[IntensityPeriod], deviation_pct: float, window_days: int
) -> Anomaly | None:
    """Rule 1: latest observed half-hour vs the norm for its time of day."""
    latest = _latest_observed(periods)
    if latest is None or latest.intensity.actual is None:
        return None
    slot: time = latest.start.timetz()
    peers = [
        value
        for period in periods
        if period is not latest
        and period.start.timetz() == slot
        and (value := _actual(period.intensity)) is not None
    ]
    if not peers:
        return None
    baseline = mean(peers)
    if baseline == 0:
        return None
    observed = latest.intensity.actual
    change = (observed - baseline) / baseline * 100
    if abs(change) < deviation_pct:
        return None
    direction = "above" if change > 0 else "below"
    return Anomaly(
        rule="intensity_deviation",
        severity="watch",
        message=(
            f"Latest intensity {observed} gCO2/kWh at {slot.strftime('%H:%M')} is "
            f"{abs(change):.0f}% {direction} the {window_days}-day norm of "
            f"{baseline:.0f} for this time of day — worth verifying."
        ),
        observed=float(observed),
        baseline=round(baseline, 2),
        detail={"change_pct": round(change, 2), "threshold_pct": deviation_pct},
    )


def _record_period(
    periods: list[IntensityPeriod], generation: list[GenerationPeriod]
) -> list[Anomaly]:
    """Rule 2: does the latest reading set a window extreme?"""
    flags: list[Anomaly] = []

    valued = [
        (period, value) for period in periods if (value := _actual(period.intensity)) is not None
    ]
    latest = _latest_observed(periods)
    values = [value for _period, value in valued]
    # Only a window with at least two readings AND real variation can hold a
    # "record": a single period, or a perfectly flat window, sets nothing — so we
    # require len > 1 and min != max before flagging, avoiding spurious records on
    # degenerate inputs.
    if (
        len(values) > 1
        and min(values) != max(values)
        and latest is not None
        and latest.intensity.actual is not None
    ):
        observed = latest.intensity.actual
        if observed == min(values):
            flags.append(
                Anomaly(
                    rule="record_period",
                    severity="info",
                    message=(
                        f"Latest half-hour is the cleanest in the window at "
                        f"{observed} gCO2/kWh — a record low."
                    ),
                    observed=float(observed),
                    baseline=float(max(values)),
                    detail={"window_min": float(min(values)), "window_max": float(max(values))},
                )
            )
        elif observed == max(values):
            flags.append(
                Anomaly(
                    rule="record_period",
                    severity="info",
                    message=(
                        f"Latest half-hour is the dirtiest in the window at "
                        f"{observed} gCO2/kWh — a record high."
                    ),
                    observed=float(observed),
                    baseline=float(min(values)),
                    detail={"window_min": float(min(values)), "window_max": float(max(values))},
                )
            )

    if generation:
        shares = [(period, _renewable_share(period)) for period in generation]
        latest_gen, latest_share = shares[-1]
        window_max = max(share for _period, share in shares)
        if latest_share == window_max and len(shares) > 1:
            flags.append(
                Anomaly(
                    rule="record_period",
                    severity="info",
                    message=(
                        f"Latest mix sets a window high for renewables at {latest_share:.1f}%."
                    ),
                    observed=round(latest_share, 2),
                    baseline=round(mean(share for _p, share in shares), 2),
                    detail={"window_max_renewable": round(window_max, 2)},
                )
            )
    return flags


def _fuel_swing(generation: list[GenerationPeriod], swing_pp: float) -> list[Anomaly]:
    """Rule 3: a fuel's latest share swings far from its window average."""
    if len(generation) < 2:
        return []

    latest = generation[-1]
    history = generation[:-1]
    flags: list[Anomaly] = []
    for fuel in latest.generationmix:
        peers = [
            other.perc
            for period in history
            for other in period.generationmix
            if other.fuel == fuel.fuel
        ]
        if not peers:
            continue
        baseline = mean(peers)
        swing = fuel.perc - baseline
        if abs(swing) < swing_pp:
            continue
        direction = "up" if swing > 0 else "down"
        flags.append(
            Anomaly(
                rule="fuel_swing",
                severity="watch",
                message=(
                    f"{fuel.fuel.title()} share is {abs(swing):.1f}pp {direction} vs its "
                    f"recent average ({fuel.perc:.1f}% now, {baseline:.1f}% typical) "
                    f"— worth verifying."
                ),
                observed=round(fuel.perc, 2),
                baseline=round(baseline, 2),
                detail={"swing_pp": round(swing, 2), "threshold_pp": swing_pp},
            )
        )
    return flags


def detect(
    periods: list[IntensityPeriod],
    generation: list[GenerationPeriod] | None = None,
    *,
    deviation_pct: float = 15.0,
    window_days: int = 7,
    swing_pp: float = 15.0,
) -> list[Anomaly]:
    """Run all anomaly rules and return the flags (empty list if all is normal)."""
    generation = generation or []
    flags: list[Anomaly] = []

    deviation = _intensity_deviation(periods, deviation_pct, window_days)
    if deviation is not None:
        flags.append(deviation)
    flags.extend(_record_period(periods, generation))
    flags.extend(_fuel_swing(generation, swing_pp))
    return flags
