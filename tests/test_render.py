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


class _CanvasWrapperChecker(HTMLParser):
    """Records any <canvas> whose direct parent isn't a bounded, positioned div."""

    _VOID = {"meta", "link", "br", "img", "input", "hr", "source", "area", "base", "col", "embed"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, dict[str, str | None]]] = []
        self.canvas_count = 0
        self.unwrapped: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "canvas":
            self.canvas_count += 1
            parent_tag, parent_attrs = self.stack[-1] if self.stack else ("", {})
            style = (parent_attrs.get("style") or "").replace(" ", "").lower()
            wrapped = (
                parent_tag == "div"
                and "position:relative" in style
                and re.search(r"height:\d+px", style) is not None
            )
            if not wrapped:
                self.unwrapped.append(attributes.get("id") or "<canvas>")
        if tag not in self._VOID:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def test_every_canvas_is_in_a_bounded_wrapper(sample_report: DashboardReport) -> None:
    # Regression guard for the Chart.js responsive-growth loop: EVERY canvas must
    # live alone in a div with position:relative + an explicit px height, or it
    # will stretch unbounded (maintainAspectRatio:false with no bounded parent).
    checker = _CanvasWrapperChecker()
    checker.feed(_render(sample_report))
    assert checker.canvas_count >= 6  # 4 sparklines + doughnut + trend
    assert checker.unwrapped == [], f"unwrapped canvases: {checker.unwrapped}"


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
