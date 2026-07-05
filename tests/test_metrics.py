"""Metrics unit tests with hand-checked expected values (build-plan §7, §15)."""

from __future__ import annotations

from typing import Any

from gridlens.metrics import (
    build_metrics_report,
    comparison,
    intensity_metrics,
    mix_metrics,
    records,
)
from gridlens.models import (
    IntensityPeriod,
    parse_generation,
    parse_intensity,
)


def _intensity(start: str, actual: int | None, forecast: int, index: str) -> IntensityPeriod:
    """Build one intensity period from primitives, for tight unit tests."""
    return IntensityPeriod.model_validate(
        {
            "from": start,
            "to": start,
            "intensity": {"actual": actual, "forecast": forecast, "index": index},
        }
    )


def test_intensity_metrics_hand_checked() -> None:
    periods = [
        _intensity("2026-06-01T00:00Z", 100, 110, "low"),
        _intensity("2026-06-01T00:30Z", 200, 190, "high"),
        _intensity("2026-06-01T01:00Z", None, 150, "high"),  # forecast fallback
    ]
    result = intensity_metrics(periods)

    assert result.mean == 150.0  # (100 + 200 + 150) / 3
    assert result.minimum == 100
    assert result.maximum == 200
    assert result.cleanest_at is not None and result.cleanest_at.hour == 0
    assert result.dirtiest_at is not None and result.dirtiest_at.minute == 30
    assert result.n_periods == 3
    assert result.n_forecast_used == 1  # one period fell back to forecast
    # Index distribution: one "low", two "high".
    assert result.index_distribution == {"low": round(1 / 3, 4), "high": round(2 / 3, 4)}


def test_intensity_metrics_empty_is_explicit() -> None:
    result = intensity_metrics([])
    assert result.mean is None
    assert result.minimum is None
    assert result.n_periods == 0


def test_mix_metrics_hand_checked(generation_good: dict[str, Any]) -> None:
    periods = parse_generation(generation_good)
    result = mix_metrics(periods)

    # Single period, so the window-average shares equal the raw percentages.
    assert result.shares["gas"] == 32.0
    assert result.ranked[0].fuel == "gas"  # gas is the largest share
    assert result.renewable_share == 36.0  # wind 26 + solar 8 + hydro 2
    assert result.low_carbon_share == 56.0  # + nuclear 15 + biomass 5
    assert result.fossil_share == 32.0  # gas 32 + coal 0
    assert result.imports_share == 11.0
    assert result.other_share == 1.0


def test_mix_metrics_empty_mix_is_explicit() -> None:
    # Periods present but every generationmix is empty must degrade to an explicit
    # empty result, not crash with a bare KeyError.
    from gridlens.models import GenerationPeriod

    period = GenerationPeriod.model_validate(
        {"from": "2026-06-01T00:00Z", "to": "2026-06-01T00:30Z", "generationmix": []}
    )
    result = mix_metrics([period])
    assert result.shares == {}
    assert result.renewable_share is None
    assert result.ranked == []


def test_records_from_range(
    intensity_range: dict[str, Any], generation_range: dict[str, Any]
) -> None:
    intensity = parse_intensity(intensity_range)
    generation = parse_generation(generation_range)
    result = records(intensity, generation)

    # The window low/high must equal the plain min/max of the actual series.
    actuals = [p.intensity.actual for p in intensity if p.intensity.actual is not None]
    assert result.lowest_intensity == min(actuals)
    assert result.highest_intensity == max(actuals)
    assert result.highest_renewable_share is not None
    assert 0.0 <= result.highest_renewable_share <= 100.0


def test_comparison_needs_two_days(intensity_range: dict[str, Any]) -> None:
    intensity = parse_intensity(intensity_range)
    deltas = comparison(intensity, [])
    metrics = {d.metric for d in deltas}
    assert "intensity_mean" in metrics
    # A one-day slice cannot be compared day-over-day, so no deltas are produced.
    one_day = [p for p in intensity if p.start.date() == intensity[0].start.date()]
    assert comparison(one_day, []) == []


def test_temporal_series_computed(
    intensity_range: dict[str, Any], generation_range: dict[str, Any]
) -> None:
    report = build_metrics_report(
        parse_intensity(intensity_range), parse_generation(generation_range)
    )
    # Time-of-day profile: 48 local half-hour slots, chronologically ordered.
    assert len(report.time_of_day) == 48
    assert report.time_of_day[0].slot == "00:00"
    assert report.time_of_day[-1].slot == "23:30"
    assert all(p.mean is not None for p in report.time_of_day)
    # Mix over time: hourly buckets with fuel shares.
    assert report.mix_over_time
    assert "gas" in report.mix_over_time[0].shares
    # Scatter + Pearson r: one point per aligned half-hour; r in [-1, 1].
    assert report.scatter
    assert report.renewable_intensity_r is not None
    assert -1.0 <= report.renewable_intensity_r <= 1.0


def test_build_report_shape(
    intensity_range: dict[str, Any], generation_range: dict[str, Any]
) -> None:
    report = build_metrics_report(
        parse_intensity(intensity_range), parse_generation(generation_range)
    )
    assert report.n_periods > 0
    assert report.intensity.mean is not None
    assert report.mix.renewable_share is not None
    assert len(report.trend) == report.n_periods
