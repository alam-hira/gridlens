"""Engine orchestration test — the full pipeline, offline via fixtures (§14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gridlens.engine import DashboardReport, build_report, validation_markdown
from gridlens.exceptions import ConfigError

from .conftest import SAMPLE_NOW, FakeClient


def test_build_report_full_pipeline(sample_report: DashboardReport) -> None:
    report = sample_report
    assert report.profile == "gb"
    assert report.scope == "national"
    assert report.metrics.intensity.mean is not None
    assert report.metrics.mix.renewable_share is not None
    # Validation flows through end to end. The window is clamped to seven complete
    # days (7 × 48 = 336 half-hours), not the 337 the raw fixture holds.
    assert report.validation.layer_b.match_rate == 1.0
    assert report.metrics.n_periods == 336
    assert report.validation.layer_a.n_periods == 336
    assert "NESO" in report.attribution


def test_window_is_clamped_to_requested_days(sample_report: DashboardReport) -> None:
    # Regression: a 7-day report must yield exactly 7 daily points and a window
    # starting at midnight — not 8 with a stray single-sample boundary "day".
    assert len(sample_report.metrics.daily) == 7
    assert sample_report.window_from == datetime(2026, 6, 26, tzinfo=UTC)
    # Every daily rollup covers a full day (48 half-hours), none a lone sliver.
    assert all(point.min_intensity is not None for point in sample_report.metrics.daily)


def test_validation_markdown_renders(sample_report: DashboardReport) -> None:
    markdown = validation_markdown(sample_report)
    assert "Layer B" in markdown
    assert "Match rate" in markdown
    assert "Layer A" in markdown


def test_regional_profile_fails_loud() -> None:
    # Scotland is a regional profile; the engine must refuse it rather than
    # return national numbers under a regional label.
    with pytest.raises(ConfigError):
        build_report("scotland", days=7, client=FakeClient(), now=SAMPLE_NOW)  # type: ignore[arg-type]
