"""Validation tests: Layer B tight reconciliation, Layer A honest gap (§8, §15)."""

from __future__ import annotations

from typing import Any

from gridlens.models import (
    IntensityPeriod,
    StatsPeriod,
    parse_factors,
    parse_generation,
    parse_intensity,
    parse_stats,
)
from gridlens.validation import (
    map_factors_to_mix,
    reconcile_stats,
    reconstruct_intensity,
    validate,
)


def test_factor_mapping_decisions(factors: dict[str, Any]) -> None:
    mapping = map_factors_to_mix(parse_factors(factors))
    # gas uses Combined Cycle (394), not Open Cycle (651) — documented choice.
    assert mapping["gas"] == 394.0
    assert mapping["coal"] == 937.0
    assert mapping["wind"] == 0.0
    # imports = mean of the interconnector factors (Dutch 474, French 53, Irish 458).
    assert mapping["imports"] == round((474 + 53 + 458) / 3, 2)


def test_layer_b_reconciles_100pct(
    intensity_range: dict[str, Any], stats_range: dict[str, Any]
) -> None:
    result = reconcile_stats(parse_intensity(intensity_range), parse_stats(stats_range))
    assert result.windows_tested == 7
    assert result.windows_matched == 7
    assert result.match_rate == 1.0
    # Tight tolerance: no window's mean is off by more than the rounding tolerance.
    assert result.max_abs_mean_difference is not None
    assert result.max_abs_mean_difference <= result.tolerance_gco2


def test_layer_b_flags_a_mismatch() -> None:
    # A block whose stats disagree with the underlying series must NOT match.
    period = IntensityPeriod.model_validate(
        {
            "from": "2026-06-01T00:00Z",
            "to": "2026-06-01T00:30Z",
            "intensity": {"actual": 100, "forecast": 100, "index": "low"},
        }
    )
    stats = StatsPeriod.model_validate(
        {
            "from": "2026-06-01T00:00Z",
            "to": "2026-06-01T00:30Z",
            "intensity": {"max": 200, "average": 200, "min": 200, "index": "high"},
        }
    )
    result = reconcile_stats([period], [stats])
    assert result.windows_matched == 0
    assert result.match_rate == 0.0


def test_layer_a_produces_distribution(
    generation_range: dict[str, Any],
    factors: dict[str, Any],
    intensity_range: dict[str, Any],
) -> None:
    result = reconstruct_intensity(
        parse_generation(generation_range),
        parse_factors(factors),
        parse_intensity(intensity_range),
    )
    assert result.n_periods == 337
    # A full distribution is reported, not a single pass/fail.
    assert result.mean_difference is not None
    assert result.std_difference is not None and result.std_difference > 0
    assert result.min_difference is not None and result.max_difference is not None
    # The reconstruction is expected to diverge; the note must say so honestly.
    assert "indicative" in result.note.lower()
    assert result.factor_mapping["gas"] == 394.0


def test_validate_bundles_both_layers(
    intensity_range: dict[str, Any],
    stats_range: dict[str, Any],
    generation_range: dict[str, Any],
    factors: dict[str, Any],
) -> None:
    report = validate(
        parse_intensity(intensity_range),
        parse_stats(stats_range),
        parse_generation(generation_range),
        parse_factors(factors),
    )
    assert report.layer_b.match_rate == 1.0
    assert report.layer_a.n_periods == 337
    assert len(report.notes) >= 2
