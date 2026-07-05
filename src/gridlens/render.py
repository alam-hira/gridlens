"""Self-contained HTML dashboard rendering (build-plan §10).

The renderer turns a computed :class:`DashboardReport` into a single HTML file
with **everything inlined** — CSS, the vendored Chart.js, and the data baked in
at render time — so the page opens standalone with no network calls and works on
GitHub Pages offline. Inlining Chart.js (rather than referencing a sibling file)
is a deliberate, stronger reading of "self-contained": the output is one portable
file with no companions and, of course, no CDN.

Design rules applied here: forms are chosen by the data's job (parts-of-whole →
doughnut, change-over-time → line, single headline → stat tile); the fuel palette
is assigned semantically per fuel from a pre-validated categorical set; colour is
never the sole channel (every mix segment and intensity band carries a text
label, plus a data-table view); and the intensity ramp runs green (clean) → red
(dirty). Chart.js renders to ``<canvas>``, a single bitmap, so every canvas gets
``role="img"``, a descriptive ``aria-label`` and text fallback — the one
accessibility trade-off vs SVG, mitigated (build-plan §10, §17). (Provenance of
the design-skill choice is recorded in ``docs/BUILD_REPORT.md``.)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from .engine import DashboardReport
from .metrics import FOSSIL_FUELS, LOW_CARBON_FUELS, RENEWABLE_FUELS

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
_CHART_JS_PATH = _STATIC_DIR / "chart.umd.js"

# Human-readable fuel labels for the coarse mix names.
FUEL_LABELS: dict[str, str] = {
    "gas": "Gas",
    "coal": "Coal",
    "imports": "Imports",
    "nuclear": "Nuclear",
    "wind": "Wind",
    "solar": "Solar",
    "hydro": "Hydro",
    "biomass": "Biomass",
    "other": "Other",
}

# Fuel colours — assigned semantically, drawn from the dataviz skill's
# pre-validated categorical slots (light / dark steps). Identity is reinforced by
# the always-present legend and table, so colour is never the only signal.
FUEL_COLORS_LIGHT: dict[str, str] = {
    "gas": "#eb6834",  # orange — fossil, warm
    "coal": "#5b4636",  # brown
    "imports": "#e87ba4",  # magenta
    "nuclear": "#4a3aa7",  # violet
    "wind": "#2a78d6",  # blue
    "solar": "#eda100",  # yellow
    "hydro": "#1baf7a",  # aqua
    "biomass": "#008300",  # green
    "other": "#898781",  # muted grey
}
FUEL_COLORS_DARK: dict[str, str] = {
    "gas": "#d95926",
    "coal": "#8a6d55",
    "imports": "#d55181",
    "nuclear": "#9085e9",
    "wind": "#3987e5",
    "solar": "#c98500",
    "hydro": "#199e70",
    "biomass": "#2ea62e",
    "other": "#a5a39c",
}

# Intensity index bands on a green (clean) → red (dirty) ramp. Always shown with
# the band's text label so the ramp is not the sole encoding.
INDEX_COLORS: dict[str, str] = {
    "very low": "#0ca30c",
    "low": "#6cbf3f",
    "moderate": "#fab219",
    "high": "#ec835a",
    "very high": "#d03b3b",
}
_BAND_ORDER = ["very low", "low", "moderate", "high", "very high"]

# A complete UTC day is 48 half-hourly settlement periods.
_PERIODS_PER_DAY = 48

# Plain-English definitions for jargon, surfaced as accessible tooltips on the
# FIRST occurrence of each term (keyed by slug → used as the tooltip element id
# ``tt-<slug>``). One short, friendly sentence each; kept here so the wording is
# in one place and the render test can check every one resolves.
TERM_DEFS: dict[str, str] = {
    "carbon_intensity": "How much CO₂ is emitted per unit of electricity used right now.",
    "gco2_kwh": "Grams of CO₂ released to make one kilowatt-hour of electricity.",
    "generation_mix": "The share of electricity coming from each fuel or source.",
    "renewable_low_carbon": (
        "Renewable here is wind, solar and hydro; low-carbon also counts nuclear and biomass."
    ),
    "forecast_actual": "Actual is measured; forecast is the estimate for periods not yet settled.",
    "settlement_period": "The grid is measured in half-hour blocks called settlement periods.",
    "embedded_generation": (
        "Small wind and solar behind the meter that the grid doesn't directly measure."
    ),
    "interconnector_imports": (
        "Electricity imported through subsea cables from other countries' grids."
    ),
    "ccgt": "Combined-cycle gas turbines — the efficient gas plants that supply most GB gas.",
}

# Which direction of change is "good" for each compared metric (cleaner grid).
_DELTA_GOOD_WHEN_DOWN = {"intensity_mean", "fossil_share"}


def _fmt_dt(moment: datetime | None, pattern: str = "%d %b %H:%M") -> str:
    return "—" if moment is None else moment.strftime(pattern)


def _modal_band(distribution: dict[str, float]) -> str | None:
    """The intensity band the most half-hours fell into."""
    if not distribution:
        return None
    return max(distribution, key=lambda band: distribution[band])


def _delta_context(report: DashboardReport) -> dict[str, dict[str, Any]]:
    """Shape day-over-day deltas for display, tagging each as good/bad."""
    out: dict[str, dict[str, Any]] = {}
    for delta in report.metrics.comparison:
        if delta.absolute is None:
            continue
        going_down = delta.absolute < 0
        good = going_down if delta.metric in _DELTA_GOOD_WHEN_DOWN else not going_down
        out[delta.metric] = {
            "current": delta.current,
            "previous": delta.previous,
            "absolute": delta.absolute,
            "percent": delta.percent,
            "arrow": "▼" if going_down else "▲",
            "good": good if delta.absolute != 0 else None,
        }
    return out


def _build_context(report: DashboardReport) -> dict[str, Any]:
    """Flatten the report into ready-to-render primitives for the template.

    All formatting and colour assignment happen here in Python, so the template
    stays declarative and the numbers on the page come straight from the engine.
    """
    metrics = report.metrics
    intensity = metrics.intensity
    mix = metrics.mix

    modal = _modal_band(intensity.index_distribution)
    mix_rows = [
        {
            "fuel": share.fuel,
            "label": FUEL_LABELS.get(share.fuel, share.fuel.title()),
            "share": share.share,
            "color_light": FUEL_COLORS_LIGHT.get(share.fuel, "#898781"),
            "color_dark": FUEL_COLORS_DARK.get(share.fuel, "#a5a39c"),
        }
        for share in mix.ranked
    ]

    band_rows = [
        {
            "band": band,
            "share": round(intensity.index_distribution.get(band, 0.0) * 100, 1),
            "color": INDEX_COLORS[band],
        }
        for band in _BAND_ORDER
        if intensity.index_distribution.get(band)
    ]

    daily = metrics.daily
    trend = metrics.trend

    # The latest calendar day is "partial" when it holds fewer than a full day of
    # half-hours (today, still in progress). Flag it so the daily label and the
    # period-comparison make clear its mean covers only the hours so far.
    latest_day_partial = bool(daily) and daily[-1].n_periods < _PERIODS_PER_DAY
    daily_labels = [point.day.strftime("%d %b") for point in daily]
    if latest_day_partial and daily_labels:
        daily_labels[-1] += " (partial)"
    latest_day_note = (
        f"{daily[-1].n_periods} of {_PERIODS_PER_DAY} half-hours" if latest_day_partial else ""
    )

    context: dict[str, Any] = {
        "title": report.title,
        "generated_at": report.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "window_from": _fmt_dt(report.window_from, "%d %b %Y %H:%M"),
        "window_to": _fmt_dt(report.window_to, "%d %b %Y %H:%M"),
        "attribution": report.attribution,
        "n_periods": metrics.n_periods,
        "n_forecast_used": intensity.n_forecast_used,
        # KPI tiles
        "mean_intensity": None if intensity.mean is None else round(intensity.mean),
        "modal_band": modal,
        "modal_band_color": INDEX_COLORS.get(modal, "#898781") if modal else "#898781",
        "renewable_share": mix.renewable_share,
        "low_carbon_share": mix.low_carbon_share,
        "fossil_share": mix.fossil_share,
        "imports_share": mix.imports_share,
        "other_share": mix.other_share,
        "cleanest_value": intensity.minimum,
        "cleanest_at": _fmt_dt(intensity.cleanest_at),
        "dirtiest_value": intensity.maximum,
        "dirtiest_at": _fmt_dt(intensity.dirtiest_at),
        "deltas": _delta_context(report),
        # Records
        "records": {
            "lowest": metrics.records.lowest_intensity,
            "lowest_at": _fmt_dt(metrics.records.lowest_intensity_at),
            "highest": metrics.records.highest_intensity,
            "highest_at": _fmt_dt(metrics.records.highest_intensity_at),
            "greenest": metrics.records.highest_renewable_share,
            "greenest_at": _fmt_dt(metrics.records.highest_renewable_at),
        },
        # Chart data (baked in as JSON)
        "mix_rows": mix_rows,
        "band_rows": band_rows,
        "trend_labels": [point.at.strftime("%d %b %H:%M") for point in trend],
        "trend_values": [point.intensity for point in trend],
        "trend_forecast": [point.is_forecast for point in trend],
        "daily_labels": daily_labels,
        "latest_day_partial": latest_day_partial,
        "latest_day_note": latest_day_note,
        "spark_intensity": [point.mean_intensity for point in daily],
        "spark_min": [point.min_intensity for point in daily],
        "spark_renewable": [point.renewable_share for point in daily],
        "spark_low_carbon": [point.low_carbon_share for point in daily],
        # Anomalies
        "anomalies": [
            {
                "rule": anomaly.rule.replace("_", " "),
                "severity": anomaly.severity,
                "message": anomaly.message,
            }
            for anomaly in report.anomalies
        ],
        # Validation
        "validation": _validation_context(report),
        # Methodology footer
        "methodology": _methodology_notes(),
        "renewable_fuels": ", ".join(RENEWABLE_FUELS),
        "low_carbon_fuels": ", ".join(LOW_CARBON_FUELS),
        "fossil_fuels": ", ".join(FOSSIL_FUELS),
        # Jargon definitions for the accessible term tooltips.
        "term_defs": TERM_DEFS,
    }
    return context


def _validation_context(report: DashboardReport) -> dict[str, Any]:
    layer_b = report.validation.layer_b
    layer_a = report.validation.layer_a
    return {
        "match_rate_pct": None if layer_b.match_rate is None else round(layer_b.match_rate * 100),
        "windows_matched": layer_b.windows_matched,
        "windows_tested": layer_b.windows_tested,
        "tolerance": layer_b.tolerance_gco2,
        "max_abs_diff": layer_b.max_abs_mean_difference,
        "layer_a_mean": layer_a.mean_difference,
        "layer_a_mean_abs": layer_a.mean_abs_difference,
        "layer_a_std": layer_a.std_difference,
        "layer_a_min": layer_a.min_difference,
        "layer_a_max": layer_a.max_difference,
        "layer_a_outliers": len(layer_a.outliers),
        "layer_a_note": layer_a.note,
        "factor_mapping": layer_a.factor_mapping,
    }


def _methodology_notes() -> list[str]:
    """The modelling choices stated honestly in the footer (build-plan §10, §17)."""
    return [
        "Intensity uses the API's <em>actual</em> value where present and falls back "
        "to the <em>forecast</em> for recent/future half-hours; the freshness line "
        "reports how many periods are forecast-only.",
        "<strong>Biomass is counted as low-carbon</strong> (with nuclear, wind, solar "
        "and hydro). This is a modelling choice — biomass lifecycle emissions are "
        "debated — and is stated here rather than hidden.",
        "<strong>Renewable share</strong> means wind + solar + hydro only; biomass and "
        "nuclear are low-carbon but not counted as renewable.",
        "<strong>Layer A gas factor</strong> uses Combined-Cycle gas (the bulk of GB "
        "gas generation); Open-Cycle peaking is a small share the coarse mix cannot "
        "distinguish.",
        "<strong>Layer A imports factor</strong> is the mean of the interconnector "
        "factors, because the mix reports a single aggregate imports share with no "
        "source split — the main reason Layer A is only indicative.",
        "Charts render to <code>&lt;canvas&gt;</code>; each carries "
        '<code>role="img"</code>, an <code>aria-label</code> and a text fallback, and '
        "a data-table view is provided for non-visual reading.",
    ]


def build_dashboard(report: DashboardReport, chart_js: str | None = None) -> str:
    """Render the report to a single, self-contained HTML string.

    ``chart_js`` defaults to the vendored ``static/chart.umd.js``; it is a
    parameter so tests can pass a tiny stub instead of inlining 200 KB.
    """
    if chart_js is None:
        chart_js = _CHART_JS_PATH.read_text(encoding="utf-8")

    # autoescape=True unconditionally: this Environment renders exactly one HTML
    # template, and `select_autoescape(["html","xml"])` would silently return
    # False for a ".j2"-suffixed name — defeating the escaping the template's
    # explicit `|safe` markers assume is on. Untrusted strings (fuel names,
    # anomaly messages) reach HTML/attribute contexts, so escaping must be the
    # default and `|safe` the exception.
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = environment.get_template("dashboard.html.j2")
    context = _build_context(report)
    context["chart_js"] = chart_js
    return template.render(**context)


def write_dashboard(report: DashboardReport, out_path: Path) -> Path:
    """Render and write the dashboard to ``out_path``; returns the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_dashboard(report), encoding="utf-8")
    return out_path
