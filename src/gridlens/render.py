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
from .metrics import FOSSIL_FUELS, LONDON, LOW_CARBON_FUELS, RENEWABLE_FUELS

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

# Fuel colours — assigned semantically per fuel, but MUTED (lower saturation) so
# the doughnut and stacked area read gently. Each fuel keeps its conventional hue
# family; the dark steps are slightly brighter equivalents tuned for the dark
# surface. Verified distinguishable: the closest CIE76 ΔE across all pairs is ~16
# (biomass/hydro), well above the ~12 merge threshold, in both themes. Identity is
# reinforced by the always-present legend and data table, so colour is never the
# only signal.
FUEL_COLORS_LIGHT: dict[str, str] = {
    "gas": "#d98e63",  # muted orange — fossil, warm
    "coal": "#8d7663",  # soft brown
    "imports": "#c894ab",  # dusty rose / mauve
    "nuclear": "#9a8cc4",  # muted violet
    "wind": "#7aa5d8",  # soft blue
    "solar": "#d9b04c",  # muted amber
    "hydro": "#6cb8a3",  # soft teal
    "biomass": "#82ab7d",  # muted green
    "other": "#a8a49c",  # warm grey
}
FUEL_COLORS_DARK: dict[str, str] = {
    "gas": "#e2a37e",
    "coal": "#a58e7b",
    "imports": "#d6a7bc",
    "nuclear": "#ada0d6",
    "wind": "#93b6e2",
    "solar": "#e5c169",
    "hydro": "#83c8b4",
    "biomass": "#98bd92",
    "other": "#bcb8b0",
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


def _fmt_local(moment: datetime | None, pattern: str = "%d %b %H:%M") -> str:
    """Format a UTC moment in UK local time (Europe/London) for reader-facing 'when'."""
    return "—" if moment is None else moment.astimezone(LONDON).strftime(pattern)


def _modal_band(distribution: dict[str, float]) -> str | None:
    """The intensity band the most half-hours fell into."""
    if not distribution:
        return None
    return max(distribution, key=lambda band: distribution[band])


def _delta_context(report: DashboardReport) -> dict[str, dict[str, Any]]:
    """Shape day-over-day deltas for display, tagging each better/worse for the grid.

    Deltas are coloured by *meaning*, not direction: a change is "better" when it
    makes the grid cleaner (intensity or fossil share falling; renewable or
    low-carbon share rising) and "worse" otherwise. The ``word`` mirrors the colour
    so a green ▲ still reads clearly as an improvement.
    """
    out: dict[str, dict[str, Any]] = {}
    for delta in report.metrics.comparison:
        if delta.absolute is None:
            continue
        going_down = delta.absolute < 0
        is_flat = delta.absolute == 0
        good = going_down if delta.metric in _DELTA_GOOD_WHEN_DOWN else not going_down
        out[delta.metric] = {
            "current": delta.current,
            "previous": delta.previous,
            "absolute": delta.absolute,
            "percent": delta.percent,
            "arrow": "▼" if going_down else "▲",
            "good": None if is_flat else good,
            "word": "" if is_flat else ("better" if good else "worse"),
        }
    return out


def _build_summary(report: DashboardReport) -> str:
    """Assemble the at-a-glance summary deterministically from computed metrics.

    Fixed sentence templates with simple conditional branches only — no free text
    — so the same metrics always produce the same words. Cleanest/dirtiest moments
    are given in UK local time (weekday + time).
    """
    metrics = report.metrics
    intensity = metrics.intensity
    if intensity.mean is None:
        return ""

    sentences: list[str] = []
    band = _modal_band(intensity.index_distribution)
    band_clause = f", mostly in the {band} band" if band else ""
    sentences.append(
        f"Over this window the grid averaged {round(intensity.mean)} gCO₂/kWh{band_clause}."
    )

    if metrics.mix.renewable_share is not None:
        sentences.append(
            f"Renewables (wind, solar, hydro) supplied {metrics.mix.renewable_share}% on average."
        )

    if intensity.cleanest_at is not None and intensity.dirtiest_at is not None:
        sentences.append(
            f"It was cleanest at {_fmt_local(intensity.cleanest_at, '%a %H:%M')} "
            f"({intensity.minimum} gCO₂/kWh) and dirtiest at "
            f"{_fmt_local(intensity.dirtiest_at, '%a %H:%M')} ({intensity.maximum}), UK time."
        )

    if metrics.daily and metrics.daily[-1].mean_intensity is not None:
        latest = metrics.daily[-1]
        latest_mean = latest.mean_intensity
        assert latest_mean is not None  # narrowed above for mypy
        if latest_mean < intensity.mean:
            direction = "cleaner than"
        elif latest_mean > intensity.mean:
            direction = "dirtier than"
        else:
            direction = "in line with"
        partial = " (partial)" if latest.n_periods < _PERIODS_PER_DAY else ""
        sentences.append(
            f"The latest day{partial} is running {direction} the window average "
            f"({round(latest_mean)} vs {round(intensity.mean)} gCO₂/kWh)."
        )

    return " ".join(sentences)


def _scatter_caption(coefficient: float | None, n: int) -> str:
    """One deterministic sentence describing the renewable–intensity correlation."""
    if coefficient is None:
        return "Not enough variation in this window to compute a correlation."
    strength = (
        "strong" if abs(coefficient) >= 0.7 else "moderate" if abs(coefficient) >= 0.4 else "weak"
    )
    sign = "negative" if coefficient < 0 else "positive"
    lower_higher = "lower" if coefficient < 0 else "higher"
    return (
        f"Pearson r = {coefficient} across {n} half-hours — a {strength} {sign} correlation: "
        f"more renewables, {lower_higher} intensity."
    )


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

    # --- Time-of-day profile (local half-hour slots) --------------------------
    tod = metrics.time_of_day
    slot_means = [(point.slot, point.mean) for point in tod if point.mean is not None]
    if slot_means:
        cleanest = min(slot_means, key=lambda item: item[1])
        dirtiest = max(slot_means, key=lambda item: item[1])
        tod_caption = (
            f"Typically cleanest around {cleanest[0]} ({round(cleanest[1])} gCO₂/kWh) and "
            f"dirtiest around {dirtiest[0]} ({round(dirtiest[1])}), UK local time."
        )
    else:
        tod_caption = ""

    # --- Generation mix over time (per-fuel stacked series, ranked order) -----
    mot = metrics.mix_over_time
    mot_series = [
        {
            "fuel": share.fuel,
            "label": FUEL_LABELS.get(share.fuel, share.fuel.title()),
            "color_light": FUEL_COLORS_LIGHT.get(share.fuel, "#898781"),
            "color_dark": FUEL_COLORS_DARK.get(share.fuel, "#a5a39c"),
            "data": [point.shares.get(share.fuel, 0.0) for point in mot],
        }
        for share in mix.ranked
    ]

    # --- Renewables vs intensity scatter --------------------------------------
    scatter_points = [{"x": point.renewable, "y": point.intensity} for point in metrics.scatter]
    scatter_caption = _scatter_caption(metrics.renewable_intensity_r, len(scatter_points))

    context: dict[str, Any] = {
        "title": report.title,
        "generated_at": report.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "window_from": _fmt_dt(report.window_from, "%d %b %Y %H:%M"),
        "window_to": _fmt_dt(report.window_to, "%d %b %Y %H:%M"),
        "attribution": report.attribution,
        "n_periods": metrics.n_periods,
        "n_forecast_used": intensity.n_forecast_used,
        # At-a-glance summary (deterministic sentences from the metrics).
        "summary": _build_summary(report),
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
        "cleanest_at": _fmt_local(intensity.cleanest_at),
        "dirtiest_value": intensity.maximum,
        "dirtiest_at": _fmt_local(intensity.dirtiest_at),
        "deltas": _delta_context(report),
        # Records (times in UK local, matching the summary and time-of-day chart)
        "records": {
            "lowest": metrics.records.lowest_intensity,
            "lowest_at": _fmt_local(metrics.records.lowest_intensity_at),
            "highest": metrics.records.highest_intensity,
            "highest_at": _fmt_local(metrics.records.highest_intensity_at),
            "greenest": metrics.records.highest_renewable_share,
            "greenest_at": _fmt_local(metrics.records.highest_renewable_at),
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
        # Time-of-day profile (48 local half-hour slots) with min–max band
        "tod_labels": [point.slot for point in tod],
        "tod_mean": [point.mean for point in tod],
        "tod_min": [point.minimum for point in tod],
        "tod_max": [point.maximum for point in tod],
        "tod_caption": tod_caption,
        "tod_rows": [
            {"slot": point.slot, "mean": point.mean, "min": point.minimum, "max": point.maximum}
            for point in tod
        ],
        # Generation mix over time (hourly) + per-fuel stacked series
        "mot_labels": [point.at.strftime("%d %b %H:%M") for point in mot],
        "mot_series": mot_series,
        # Renewables vs intensity scatter
        "scatter_points": scatter_points,
        "scatter_caption": scatter_caption,
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
        # Per-half-hour reconstruction gap, for the "gap over time" chart.
        "gap_labels": [gap.at.strftime("%d %b %H:%M") for gap in layer_a.gaps],
        "gap_data": [gap.difference for gap in layer_a.gaps],
    }


def _methodology_notes() -> list[str]:
    """The modelling choices, plain-language first (build-plan §10, §17).

    Each choice is one plain sentence for a general reader, with the precise bit
    kept in place rather than hidden — the reader never has to know the internal
    "Layer A/B" names to understand what was done.
    """
    return [
        "We show the grid's <em>measured</em> intensity where it's available, and the "
        "<em>forecast</em> for the latest half-hours that aren't final yet — the line at the "
        "top counts how many are forecast.",
        "<strong>Biomass is counted as low-carbon</strong> (alongside nuclear, wind, solar and "
        "hydro). That's a judgement call — biomass's lifecycle emissions are debated — so we "
        "state it plainly rather than bury it.",
        "<strong>Renewable</strong> here means wind, solar and hydro only; nuclear and biomass "
        "count as low-carbon but not renewable.",
        "For the from-scratch estimate, gas uses the emissions factor for the efficient "
        "combined-cycle plants that supply most GB gas; the public mix can't separate the "
        "smaller peaking plants.",
        "The public mix lumps all imports into one figure, so the estimate averages the "
        "interconnector factors — a rough proxy, and the main reason the from-scratch check is "
        "only indicative.",
        "Charts are drawn as a <code>&lt;canvas&gt;</code> image, so each carries a text label "
        '(<code>role="img"</code> + <code>aria-label</code>) and a data-table fallback for '
        "non-visual reading.",
        "Day-to-day changes are coloured by whether they're <strong>better or worse for grid "
        "cleanliness</strong>, not by direction — so a fall in intensity (cleaner) shows green "
        "even though its arrow points down.",
        "The grid data is published in UTC; the <strong>time-of-day profile and the "
        "cleanest/dirtiest times are converted to UK local time</strong> (Europe/London, so BST "
        "in summer) so daylight lines up with the clock.",
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
