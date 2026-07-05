"""Dashboard render tests — self-contained, accessible, well-formed (§10, §15)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

from gridlens.engine import DashboardReport
from gridlens.metrics import build_metrics_report
from gridlens.models import Delta, MetricsReport, parse_intensity
from gridlens.render import TERM_DEFS, build_dashboard


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


def test_delta_colours_are_labelled_by_meaning() -> None:
    # Deltas are coloured by better/worse-for-the-grid, not direction; each KPI
    # delta must carry a matching "(better)"/"(worse)" word so a green ▲ is clear.
    comparison = [
        Delta(metric="intensity_mean", current=110, previous=100, absolute=10.0, percent=10.0),
        Delta(metric="renewable_share", current=45, previous=40, absolute=5.0, percent=12.5),
        Delta(metric="low_carbon_share", current=55, previous=50, absolute=5.0, percent=10.0),
        Delta(metric="fossil_share", current=30, previous=35, absolute=-5.0, percent=-14.29),
    ]
    report = DashboardReport(
        profile="gb",
        title="T",
        scope="national",
        generated_at=datetime(2026, 7, 3, tzinfo=UTC),
        metrics=MetricsReport(comparison=comparison),
    )
    html = _render(report)

    def kpi(needle: str) -> tuple[str, str]:
        for cls, text in re.findall(r'<span class="delta (delta-\w+)">(.*?)</span>', html, re.S):
            if needle in text:
                return cls, text
        raise AssertionError(f"no KPI delta matching {needle!r}")

    # Intensity UP is worse for the grid → red + "(worse)".
    cls, text = kpi("gCO₂/kWh vs prev day mean")
    assert cls == "delta-bad" and "(worse)" in text
    # Renewable UP and low-carbon UP are better → green + "(better)".
    cls, text = kpi("pp vs prev day mean")
    assert cls == "delta-good" and "(better)" in text
    cls, text = kpi("low-carbon vs prev day mean")
    assert cls == "delta-good" and "(better)" in text

    # Fossil DOWN is better → green in the comparison-table Change cell.
    row = re.search(r"<td>fossil share</td>.*?<td class=\"num (delta-\w+)\">", html, re.S)
    assert row is not None and row.group(1) == "delta-good"

    # Invariant: every KPI delta's colour class matches its word.
    for cls, text in re.findall(r'<span class="delta (delta-\w+)">(.*?)</span>', html, re.S):
        if "(better)" in text:
            assert cls == "delta-good"
        if "(worse)" in text:
            assert cls == "delta-bad"


def test_at_a_glance_summary_renders_with_computed_mean(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    match = re.search(r'<p class="glance">(.*?)</p>', html, re.S)
    assert match is not None, "at-a-glance summary not rendered"
    glance = match.group(1)
    mean = sample_report.metrics.intensity.mean
    assert mean is not None
    # The summary quotes the deterministically-computed window mean.
    assert str(round(mean)) in glance
    assert "gCO₂/kWh" in glance


def test_new_temporal_charts_present(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    # Four new canvases (time-of-day, mix-over-time, scatter, gap) on top of the 6.
    for canvas_id in ("todChart", "motChart", "scatterChart", "gapChart"):
        assert f'id="{canvas_id}"' in html
    assert html.count("<canvas") == 10
    # Local-time axis and the Pearson r caption are surfaced.
    assert "UK local time" in html
    assert "Pearson r" in html


def test_methodology_uses_progressive_disclosure(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    # Both plain-English headlines are present.
    assert "did we get the maths right?" in html
    assert "can we rebuild the number from scratch?" in html
    # Both tiles expose a native <details> with the precise method.
    assert html.count("<summary>The precise method</summary>") == 2
    # The rigorous content/numbers were MOVED, not removed — still in the output.
    assert "/intensity/stats" in html  # Layer B precise reconciliation
    assert "outliers flagged" in html  # Layer A precise distribution
    assert "% match" in html  # headline number stays prominent
    assert "mean gap" in html  # headline number stays prominent


def test_terms_have_accessible_descriptions(sample_report: DashboardReport) -> None:
    # Jargon is explained via inline tooltips only (no glossary section): every
    # marked term must carry aria-describedby pointing at a tooltip whose text is
    # non-empty, so screen readers announce the definition on focus.
    html = _render(sample_report)
    described = set(re.findall(r'aria-describedby="([^"]+)"', html))
    assert described, "no terms with aria-describedby found"
    for ref in described:
        match = re.search(rf'id="{re.escape(ref)}"[^>]*>(.*?)</span>', html, re.S)
        assert match is not None, f"aria-describedby={ref} resolves to no element"
        assert match.group(1).strip(), f"description for {ref} is empty"
    # All nine expected jargon terms are marked up and described.
    for key in TERM_DEFS:
        assert f'aria-describedby="tt-{key}"' in html, f"term {key!r} is not marked up"


def test_every_canvas_is_in_a_bounded_wrapper(sample_report: DashboardReport) -> None:
    # Regression guard for the Chart.js responsive-growth loop: EVERY canvas must
    # live alone in a div with position:relative + an explicit px height, or it
    # will stretch unbounded (maintainAspectRatio:false with no bounded parent).
    checker = _CanvasWrapperChecker()
    checker.feed(_render(sample_report))
    assert checker.canvas_count >= 6  # 4 sparklines + doughnut + trend
    assert checker.unwrapped == [], f"unwrapped canvases: {checker.unwrapped}"


def test_all_displayed_times_are_uk_local(sample_report: DashboardReport) -> None:
    # Timezone coherence: every displayed time is converted to Europe/London, not
    # merely relabelled. The fixtures are summer (BST = UTC+1), so a genuine
    # conversion shifts the window-start clock by an hour — a relabel-only change
    # would leave the raw UTC time on the page.
    html = _render(sample_report)
    london = ZoneInfo("Europe/London")
    window_from = sample_report.window_from
    assert window_from is not None
    local = window_from.astimezone(london).strftime("%d %b %Y %H:%M")
    naive_utc = window_from.strftime("%d %b %Y %H:%M")
    assert local != naive_utc, "fixture window should be in BST for this test to bite"
    assert local in html, "window start is not shown in UK local time"
    assert naive_utc not in html, "raw UTC time leaked onto the page (relabelled, not converted)"
    # The reader is told which clock the page uses.
    assert "All times UK local (Europe/London)" in html


def test_section_nav_links_to_existing_ids(sample_report: DashboardReport) -> None:
    # The sticky nav's anchor links must each resolve to a real section id.
    html = _render(sample_report)
    assert 'class="section-nav"' in html
    for slug in ("overview", "trends", "anomalies", "methodology", "data"):
        assert f'href="#{slug}"' in html, f"nav link for {slug!r} missing"
        assert f'id="{slug}"' in html, f"section target {slug!r} missing"


def test_share_meta_and_inline_favicon_present(sample_report: DashboardReport) -> None:
    html = _render(sample_report)
    assert '<meta name="description"' in html
    assert 'property="og:title" content="GridLens — GB electricity dashboard"' in html
    assert 'property="og:description"' in html
    assert 'property="og:type" content="website"' in html
    # Favicon is an inline SVG data URI — still self-contained, no companion file.
    assert 'rel="icon" href="data:image/svg+xml,' in html


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
