"""Anomaly tests: each rule must both fire and stay quiet (build-plan §9, §15)."""

from __future__ import annotations

from gridlens.anomalies import detect
from gridlens.models import GenerationPeriod, IntensityPeriod


def _intensity(start: str, actual: int) -> IntensityPeriod:
    return IntensityPeriod.model_validate(
        {"from": start, "to": start, "intensity": {"actual": actual, "forecast": actual}}
    )


def _generation(start: str, gas: float, wind: float) -> GenerationPeriod:
    return GenerationPeriod.model_validate(
        {
            "from": start,
            "to": start,
            "generationmix": [
                {"fuel": "gas", "perc": gas},
                {"fuel": "wind", "perc": wind},
            ],
        }
    )


def _rules(anomalies: list) -> set[str]:
    return {a.rule for a in anomalies}


# --- Rule 1: intensity deviation --------------------------------------------


def test_intensity_deviation_fires() -> None:
    # Same 12:00 slot on three days; the latest is double the recent norm.
    periods = [
        _intensity("2026-06-01T12:00Z", 100),
        _intensity("2026-06-02T12:00Z", 100),
        _intensity("2026-06-03T12:00Z", 200),
    ]
    anomalies = detect(periods, deviation_pct=15.0)
    dev = [a for a in anomalies if a.rule == "intensity_deviation"]
    assert dev, "expected an intensity_deviation flag"
    assert dev[0].observed == 200.0
    assert dev[0].baseline == 100.0


def test_intensity_deviation_quiet_when_normal() -> None:
    periods = [
        _intensity("2026-06-01T12:00Z", 100),
        _intensity("2026-06-02T12:00Z", 100),
        _intensity("2026-06-03T12:00Z", 105),  # +5% only
    ]
    assert "intensity_deviation" not in _rules(detect(periods, deviation_pct=15.0))


# --- Rule 2: record period --------------------------------------------------


def test_record_low_fires() -> None:
    # Distinct slots (so the deviation rule stays out of it); latest is the min.
    periods = [
        _intensity("2026-06-03T10:00Z", 200),
        _intensity("2026-06-03T11:00Z", 150),
        _intensity("2026-06-03T12:00Z", 100),  # window low
    ]
    anomalies = detect(periods)
    records = [a for a in anomalies if a.rule == "record_period"]
    assert records and "record low" in records[0].message.lower()


# --- Rule 3: fuel-share swing -----------------------------------------------


def test_fuel_swing_fires() -> None:
    generation = [
        _generation("2026-06-03T10:00Z", gas=30, wind=20),
        _generation("2026-06-03T11:00Z", gas=30, wind=20),
        _generation("2026-06-03T12:00Z", gas=60, wind=20),  # gas +30pp
    ]
    anomalies = detect([], generation, swing_pp=15.0)
    swings = [a for a in anomalies if a.rule == "fuel_swing"]
    assert any(a.observed == 60.0 for a in swings)


def test_fuel_swing_quiet_when_stable() -> None:
    generation = [
        _generation("2026-06-03T10:00Z", gas=30, wind=20),
        _generation("2026-06-03T11:00Z", gas=30, wind=20),
        _generation("2026-06-03T12:00Z", gas=32, wind=20),  # +2pp only
    ]
    assert "fuel_swing" not in _rules(detect([], generation, swing_pp=15.0))


def test_no_anomalies_on_empty() -> None:
    assert detect([]) == []


def test_record_not_flagged_on_single_period() -> None:
    # One reading holds no "record" — the guard must suppress a spurious flag.
    assert "record_period" not in _rules(detect([_intensity("2026-06-03T12:00Z", 123)]))


def test_record_not_flagged_on_flat_window() -> None:
    # A perfectly flat window (min == max) sets no record either.
    periods = [
        _intensity("2026-06-03T10:00Z", 100),
        _intensity("2026-06-03T11:00Z", 100),
        _intensity("2026-06-03T12:00Z", 100),
    ]
    assert "record_period" not in _rules(detect(periods))
