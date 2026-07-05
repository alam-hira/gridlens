"""Deterministic metric calculations (build-plan §7).

All maths lives here, in code — never delegated to a model. Each function takes
validated Pydantic models in and returns a typed result model out, so a wrong
number can only come from a bug in this file, which the unit tests pin down with
hand-checked expected values.

Design notes
------------
* **"Actual where present, else forecast."** Recent/future half-hours have a
  ``forecast`` but a null ``actual``. Every intensity figure uses the actual and
  falls back to the forecast, recording how many fell back so the dashboard can
  label "actual vs forecast" honestly (build-plan §7, §17).
* **pandas for genuine aggregation.** The generation mix (many fuels × many
  half-hours → a mean per fuel) and the day-over-day grouping are done with
  pandas, which is the standard tool for this shape of work. The intensity
  extremes are a simple min/max with an argmin/argmax, which is clearer in plain
  Python — so we keep it there rather than force pandas on it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from statistics import StatisticsError, correlation, mean
from zoneinfo import ZoneInfo

import pandas as pd

from .models import (
    DailyPoint,
    Delta,
    FuelShare,
    GenerationPeriod,
    IntensityMetrics,
    IntensityPeriod,
    IntensityValue,
    MetricsReport,
    MixMetrics,
    MixOverTimePoint,
    Records,
    ScatterPoint,
    TimeOfDayPoint,
    TrendPoint,
    effective_intensity,
)

# GB grid data is published in UTC; the time-of-day view is shown in local time
# so "cleanest around lunchtime" lands where a UK reader expects it.
LONDON = ZoneInfo("Europe/London")

# --- Fuel classification (a stated modelling choice; see the footer) ---------
#
# "Renewable" is the strict wind + solar + hydro. "Low-carbon" additionally
# counts nuclear and biomass. Treating biomass as low-carbon is debated (its
# lifecycle emissions are non-trivial) — the plan calls for stating it, which
# the dashboard footer does. ``imports`` and ``other`` are reported separately
# rather than forced into either bucket, because their true carbon content is
# unknown from the coarse mix alone.
RENEWABLE_FUELS = ("wind", "solar", "hydro")
LOW_CARBON_FUELS = ("wind", "solar", "hydro", "nuclear", "biomass")
FOSSIL_FUELS = ("gas", "coal")


def _effective(value: IntensityValue) -> tuple[int | None, bool]:
    """Return ``(intensity, used_forecast)`` for one period.

    Wraps the shared :func:`effective_intensity` and adds the "did we fall back to
    forecast?" flag that the dashboard's freshness line needs.
    """
    resolved = effective_intensity(value)
    used_forecast = resolved is not None and value.actual is None
    return resolved, used_forecast


def intensity_metrics(periods: list[IntensityPeriod]) -> IntensityMetrics:
    """Mean / min / max intensity, cleanest & dirtiest periods, band mix."""
    rows = [(p.start, *_effective(p.intensity), p.intensity.index) for p in periods]
    valued = [(start, value, index) for (start, value, _fc, index) in rows if value is not None]
    if not valued:
        # Fail soft into an explicit "nothing computable" rather than a fake 0.
        return IntensityMetrics(n_periods=len(periods))

    values = [value for _start, value, _index in valued]
    cleanest = min(valued, key=lambda r: r[1])
    dirtiest = max(valued, key=lambda r: r[1])

    # Distribution across index bands (only over periods that carry an index).
    bands = [index for (_start, _value, index) in valued if index]
    counts = Counter(bands)
    total_bands = sum(counts.values())
    distribution = (
        {band: round(count / total_bands, 4) for band, count in counts.items()}
        if total_bands
        else {}
    )

    return IntensityMetrics(
        mean=round(mean(values), 2),
        minimum=min(values),
        maximum=max(values),
        cleanest_at=cleanest[0],
        dirtiest_at=dirtiest[0],
        n_periods=len(periods),
        n_forecast_used=sum(1 for (_s, _v, fc, _i) in rows if fc),
        index_distribution=distribution,
    )


def mix_metrics(periods: list[GenerationPeriod]) -> MixMetrics:
    """Window-average generation mix and the derived renewable/low-carbon splits."""
    if not periods:
        return MixMetrics()

    # Explode every (period, fuel) pair into a row, then a single groupby-mean
    # gives the window-average share per fuel — the canonical pandas idiom.
    frame = pd.DataFrame(
        {"fuel": fuel.fuel, "perc": fuel.perc}
        for period in periods
        for fuel in period.generationmix
    )
    if frame.empty:
        # Periods present but every generationmix is empty: degrade to an explicit
        # empty result rather than letting the groupby raise a bare KeyError.
        return MixMetrics()
    means = frame.groupby("fuel")["perc"].mean()
    shares = {str(fuel): round(float(value), 2) for fuel, value in means.items()}

    ranked = sorted(
        (FuelShare(fuel=fuel, share=share) for fuel, share in shares.items()),
        key=lambda item: item.share,
        reverse=True,
    )

    def total(fuels: tuple[str, ...]) -> float:
        return round(sum(shares.get(fuel, 0.0) for fuel in fuels), 2)

    return MixMetrics(
        shares=shares,
        ranked=ranked,
        renewable_share=total(RENEWABLE_FUELS),
        low_carbon_share=total(LOW_CARBON_FUELS),
        fossil_share=total(FOSSIL_FUELS),
        imports_share=shares.get("imports"),
        other_share=shares.get("other"),
    )


def _renewable_share(period: GenerationPeriod) -> float:
    """Instantaneous renewable share (wind + solar + hydro) for one period."""
    return sum(fuel.perc for fuel in period.generationmix if fuel.fuel in RENEWABLE_FUELS)


def records(
    intensity_periods: list[IntensityPeriod],
    generation_periods: list[GenerationPeriod],
) -> Records:
    """Notable single-period extremes: cleanest/dirtiest half-hour, greenest mix."""
    result = Records()

    raw = [(p.start, _effective(p.intensity)[0]) for p in intensity_periods]
    valued = [(start, value) for start, value in raw if value is not None]
    if valued:
        lowest = min(valued, key=lambda r: r[1])
        highest = max(valued, key=lambda r: r[1])
        result.lowest_intensity, result.lowest_intensity_at = lowest[1], lowest[0]
        result.highest_intensity, result.highest_intensity_at = highest[1], highest[0]

    best_share: float | None = None
    greenest_at: datetime | None = None
    for period in generation_periods:
        share = _renewable_share(period)
        if best_share is None or share > best_share:
            best_share = share
            greenest_at = period.start
    if best_share is not None:
        result.highest_renewable_share = round(best_share, 2)
        result.highest_renewable_at = greenest_at

    return result


def _delta(metric: str, current: float, previous: float) -> Delta:
    """Build a :class:`Delta`, guarding against divide-by-zero on the percent."""
    absolute = current - previous
    percent = (absolute / previous * 100) if previous else None
    return Delta(
        metric=metric,
        current=round(current, 2),
        previous=round(previous, 2),
        absolute=round(absolute, 2),
        percent=round(percent, 2) if percent is not None else None,
    )


def _daily_means(pairs: Sequence[tuple[date, float]]) -> dict[date, float]:
    """Mean value per calendar day from ``(day, value)`` pairs."""
    grouped: dict[date, list[float]] = defaultdict(list)
    for day, value in pairs:
        grouped[day].append(value)
    return {day: mean(values) for day, values in grouped.items()}


def comparison(
    intensity_periods: list[IntensityPeriod],
    generation_periods: list[GenerationPeriod],
) -> list[Delta]:
    """Day-over-day deltas: the two most recent calendar days present.

    Compares the latest calendar day in the window against the day before it
    (build-plan §7 "today vs yesterday"). The latest day may be a partial current
    day in a live report, so this is "most-recent day vs previous day", not
    necessarily two complete days. Returns an empty list when fewer than two days
    are available rather than fabricating a comparison.
    """
    deltas: list[Delta] = []

    intensity_pairs = [
        (p.start.date(), value)
        for p in intensity_periods
        if (value := _effective(p.intensity)[0]) is not None
    ]
    intensity_by_day = _daily_means(intensity_pairs)
    days = sorted(intensity_by_day)
    if len(days) >= 2:
        deltas.append(
            _delta("intensity_mean", intensity_by_day[days[-1]], intensity_by_day[days[-2]])
        )

    for name, fuels in (
        ("renewable_share", RENEWABLE_FUELS),
        ("low_carbon_share", LOW_CARBON_FUELS),
        ("fossil_share", FOSSIL_FUELS),
    ):
        pairs = [
            (period.start.date(), sum(f.perc for f in period.generationmix if f.fuel in fuels))
            for period in generation_periods
        ]
        by_day = _daily_means(pairs)
        gdays = sorted(by_day)
        if len(gdays) >= 2:
            deltas.append(_delta(name, by_day[gdays[-1]], by_day[gdays[-2]]))

    return deltas


def trend(intensity_periods: list[IntensityPeriod]) -> list[TrendPoint]:
    """The half-hourly intensity series that drives the trend chart."""
    points = []
    for period in intensity_periods:
        value, used_forecast = _effective(period.intensity)
        points.append(TrendPoint(at=period.start, intensity=value, is_forecast=used_forecast))
    return points


def daily_series(
    intensity_periods: list[IntensityPeriod],
    generation_periods: list[GenerationPeriod],
) -> list[DailyPoint]:
    """Per-calendar-day rollups that drive the KPI sparklines.

    Groups both series by UTC date and, for each day present, records the mean /
    min / max intensity and the mean renewable and low-carbon shares.
    """
    intensity_by_day: dict[date, list[int]] = defaultdict(list)
    for period in intensity_periods:
        value = _effective(period.intensity)[0]
        if value is not None:
            intensity_by_day[period.start.date()].append(value)

    renewable_by_day: dict[date, list[float]] = defaultdict(list)
    low_carbon_by_day: dict[date, list[float]] = defaultdict(list)
    for gen_period in generation_periods:
        day = gen_period.start.date()
        renewable_by_day[day].append(_renewable_share(gen_period))
        low_carbon_by_day[day].append(
            sum(fuel.perc for fuel in gen_period.generationmix if fuel.fuel in LOW_CARBON_FUELS)
        )

    days = sorted(set(intensity_by_day) | set(renewable_by_day))
    points = []
    for day in days:
        intensities = intensity_by_day.get(day, [])
        renewables = renewable_by_day.get(day, [])
        low_carbons = low_carbon_by_day.get(day, [])
        # The half-hour count is taken from whichever series is present, so a
        # partial current day is flagged even if only one series covers it.
        n_periods = max(len(intensities), len(renewables))
        points.append(
            DailyPoint(
                day=day,
                n_periods=n_periods,
                mean_intensity=round(mean(intensities), 2) if intensities else None,
                min_intensity=min(intensities) if intensities else None,
                max_intensity=max(intensities) if intensities else None,
                renewable_share=round(mean(renewables), 2) if renewables else None,
                low_carbon_share=round(mean(low_carbons), 2) if low_carbons else None,
            )
        )
    return points


def time_of_day_profile(intensity_periods: list[IntensityPeriod]) -> list[TimeOfDayPoint]:
    """Mean/min/max intensity per local half-hour-of-day, averaged across the window.

    Each period's UTC start is converted to Europe/London and bucketed by its
    local ``HH:MM`` slot, so the profile answers "when in a typical UK day is the
    grid cleanest?". Slots sort chronologically because ``HH:MM`` is zero-padded.
    """
    by_slot: dict[str, list[int]] = defaultdict(list)
    for period in intensity_periods:
        value = _effective(period.intensity)[0]
        if value is not None:
            slot = period.start.astimezone(LONDON).strftime("%H:%M")
            by_slot[slot].append(value)
    return [
        TimeOfDayPoint(
            slot=slot,
            mean=round(mean(by_slot[slot]), 2),
            minimum=min(by_slot[slot]),
            maximum=max(by_slot[slot]),
        )
        for slot in sorted(by_slot)
    ]


def mix_over_time(generation_periods: list[GenerationPeriod]) -> list[MixOverTimePoint]:
    """Generation mix through the window, downsampled to hourly means for legibility."""
    by_hour: dict[datetime, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for period in generation_periods:
        hour = period.start.replace(minute=0, second=0, microsecond=0)
        for fuel in period.generationmix:
            by_hour[hour][fuel.fuel].append(fuel.perc)
    return [
        MixOverTimePoint(
            at=hour,
            shares={fuel: round(mean(values), 2) for fuel, values in by_hour[hour].items()},
        )
        for hour in sorted(by_hour)
    ]


def renewable_vs_intensity(
    intensity_periods: list[IntensityPeriod],
    generation_periods: list[GenerationPeriod],
) -> tuple[list[ScatterPoint], float | None]:
    """Pair each half-hour's renewable share with its intensity; return points + Pearson r."""
    intensity_by_start = {
        period.start: value
        for period in intensity_periods
        if (value := _effective(period.intensity)[0]) is not None
    }
    points: list[ScatterPoint] = []
    renewables: list[float] = []
    intensities: list[float] = []
    for period in generation_periods:
        intensity_value = intensity_by_start.get(period.start)
        if intensity_value is None:
            continue
        share = _renewable_share(period)
        points.append(ScatterPoint(renewable=round(share, 2), intensity=intensity_value))
        renewables.append(share)
        intensities.append(intensity_value)

    coefficient: float | None = None
    if len(renewables) >= 2:
        try:
            coefficient = round(correlation(renewables, intensities), 3)
        except StatisticsError:
            # Constant series (zero variance) has no defined correlation.
            coefficient = None
    return points, coefficient


def build_metrics_report(
    intensity_periods: list[IntensityPeriod],
    generation_periods: list[GenerationPeriod],
) -> MetricsReport:
    """Assemble the full metric bundle the API serves and the dashboard renders."""
    scatter, renewable_intensity_r = renewable_vs_intensity(intensity_periods, generation_periods)
    return MetricsReport(
        window_from=intensity_periods[0].start if intensity_periods else None,
        window_to=intensity_periods[-1].end if intensity_periods else None,
        n_periods=len(intensity_periods),
        intensity=intensity_metrics(intensity_periods),
        mix=mix_metrics(generation_periods),
        records=records(intensity_periods, generation_periods),
        comparison=comparison(intensity_periods, generation_periods),
        trend=trend(intensity_periods),
        daily=daily_series(intensity_periods, generation_periods),
        time_of_day=time_of_day_profile(intensity_periods),
        mix_over_time=mix_over_time(generation_periods),
        scatter=scatter,
        renewable_intensity_r=renewable_intensity_r,
    )
