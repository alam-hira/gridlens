"""Dashboard render tests — self-contained, accessible, well-formed (§10, §15)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from gridlens.engine import DashboardReport
from gridlens.metrics import build_metrics_report
from gridlens.models import parse_intensity
from gridlens.render import build_dashboard


def _render(report: DashboardReport) -> str:
    # A tiny stub stands in for the 200 KB vendored Chart.js so tests stay fast.
    return build_dashboard(report, chart_js="/* chart.js stub */")


def test_dashboard_is_self_contained(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    assert html.lstrip().startswith("<!doctype html>")
    # No network dependencies: no CDN, no external stylesheet/script/img src.
    assert "cdn." not in html
    assert 'src="http' not in html
    assert 'href="http' not in html.replace('href="https://www.chartjs.org"', "")
    # No leaked template artifacts.
    assert "{{" not in html and "{%" not in html and "Undefined" not in html


def test_dashboard_accessibility(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    # Every chart canvas needs role="img" + aria-label + a text fallback (§10).
    assert html.count("<canvas") >= 5
    assert html.count("<canvas") <= html.count('role="img"')
    assert html.count("aria-label") >= html.count("<canvas")


def test_dashboard_shows_validation_and_attribution(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    assert "100% match" in html  # Layer B result surfaced
    assert "indicative" in html.lower()  # Layer A honesty
    assert "NESO" in html  # required attribution
    assert "Biomass is counted as low-carbon" in html  # stated modelling choice


def test_dashboard_is_well_formed(sample_report: DashboardReport) -> None:
    # The stdlib parser raising nothing is a basic well-formedness signal.
    HTMLParser().feed(_render(sample_report))


def test_dashboard_labels_forecast_when_actual_null(intensity_date: dict[str, Any]) -> None:
    # A window with a forecast-only tail (actual=null) must render, count the
    # forecast periods in the freshness line, and mark them for the dashed trend.
    metrics = build_metrics_report(parse_intensity(intensity_date), [])
    assert metrics.intensity.n_forecast_used > 0
    report = DashboardReport(
        profile="gb",
        title="Great Britain electricity",
        scope="national",
        generated_at=datetime(2026, 7, 3, tzinfo=UTC),
        window_from=metrics.window_from,
        window_to=metrics.window_to,
        metrics=metrics,
    )
    html = _render(report)
    assert "forecast-only" in html
    forecast_flags = json.loads(re.search(r"trendForecast: (\[.*?\]),", html).group(1))
    assert any(forecast_flags)  # at least one half-hour marked forecast
