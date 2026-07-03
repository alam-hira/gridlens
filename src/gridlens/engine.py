"""Orchestration seam: fetch → compute → bundle (a single code path).

This module is a small addition to the module list in build-plan §6. Its job is
to be the *one* place that turns "profile + window" into a fully-computed
:class:`DashboardReport`, so the CLI, the API, and the dashboard renderer all
share exactly one fetch-and-compute path (single source of truth for every
number). It contains no maths of its own — it only calls the engine modules
(``metrics``, ``anomalies``, ``validation``) and stitches their typed results
together.

Everything is injectable (``client``, ``settings``, ``now``) so the whole
pipeline can be exercised offline against fixtures — the tests never touch the
network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .anomalies import Anomaly, detect
from .client import CarbonIntensityClient
from .config import Profile, Settings, load_profile
from .exceptions import ConfigError
from .logging_config import get_logger
from .metrics import build_metrics_report
from .models import MetricsReport
from .validation import ValidationReport, validate

logger = get_logger(__name__)

# NESO CC BY 4.0 attribution (required — build-plan §4.4). Shown in every output.
ATTRIBUTION = (
    "Carbon intensity and generation data © National Energy System Operator "
    "(NESO), via the Carbon Intensity API (carbonintensity.org.uk), used under "
    "CC BY 4.0."
)

_API_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"


class DashboardReport(BaseModel):
    """The complete, computed result for one profile and window.

    This is the object the API serialises, the CLI prints from, and the renderer
    turns into HTML — so the JSON and the dashboard can never disagree.
    """

    profile: str
    title: str
    scope: str
    generated_at: datetime
    window_from: datetime | None = None
    window_to: datetime | None = None
    metrics: MetricsReport = Field(default_factory=MetricsReport)
    anomalies: list[Anomaly] = Field(default_factory=list)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    attribution: str = ATTRIBUTION


def _fmt(moment: datetime) -> str:
    """Format a datetime the way the Carbon Intensity API expects."""
    return moment.strftime(_API_TIME_FORMAT)


def _window(now: datetime, days: int) -> tuple[datetime, datetime, datetime]:
    """Return (window_start, last_midnight, end) for a ``days``-day window.

    * ``window_start`` — midnight ``days`` days ago (start of the report window).
    * ``last_midnight`` — start of *today*; the reconciled statistics cover only
      the complete days up to here, so Layer B never reconciles a partial day.
    * ``end`` — ``now`` (so the report itself can include today's data so far).
    """
    last_midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
    window_start = last_midnight - timedelta(days=days)
    return window_start, last_midnight, now


def build_report(
    profile_name: str = "gb",
    days: int = 7,
    *,
    client: CarbonIntensityClient | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DashboardReport:
    """Fetch a window's data and compute the full report bundle.

    Regional profiles are *beta* in this tier (build-plan §17): the client only
    implements the national endpoints, so a regional profile fails loud here
    rather than silently returning national numbers under a regional label.
    """
    settings = settings or Settings()
    client = client or CarbonIntensityClient(settings)
    now = now or datetime.now(UTC)
    profile: Profile = load_profile(profile_name)

    if profile.scope != "national":
        raise ConfigError(
            f"Profile {profile_name!r} is {profile.scope!r}; regional analytics are "
            "beta and not wired into the engine in this tier (build-plan §17)."
        )

    window_start, last_midnight, end = _window(now, days)
    logger.info(
        "Building report for profile=%s window=%s..%s", profile_name, _fmt(window_start), _fmt(end)
    )

    intensity = client.intensity(_fmt(window_start), _fmt(end))
    generation = client.generation(_fmt(window_start), _fmt(end))
    # The API's range endpoints can return a boundary settlement period that
    # starts just before `window_start` (the half-hour *ending* at `from`). Clamp
    # both series to the exact requested window so a stray half-hour can't become
    # a spurious single-sample "day" in the daily/sparkline series or drag the
    # reported window start back by 30 minutes.
    intensity = [period for period in intensity if window_start <= period.start < end]
    generation = [period for period in generation if window_start <= period.start < end]
    # Statistics only over complete days, so Layer B reconciles whole-day blocks.
    stats = client.stats(_fmt(window_start), _fmt(last_midnight), block_hours=24)
    factors = client.factors()

    metrics = build_metrics_report(intensity, generation)
    anomalies = detect(
        intensity,
        generation,
        deviation_pct=settings.anomaly_deviation_pct,
        window_days=days,
        swing_pp=settings.anomaly_swing_pp,
    )
    validation = validate(intensity, stats, generation, factors)

    return DashboardReport(
        profile=profile_name,
        title=profile.labels.get("title", profile.name),
        scope=profile.scope,
        generated_at=now,
        window_from=metrics.window_from,
        window_to=metrics.window_to,
        metrics=metrics,
        anomalies=anomalies,
        validation=validation,
    )


def validation_markdown(report: DashboardReport) -> str:
    """Render the validation result as the Markdown report saved to examples/."""
    layer_b = report.validation.layer_b
    layer_a = report.validation.layer_a
    lines = [
        f"# GridLens validation report — {report.title}",
        "",
        f"*Generated {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')} · "
        f"window {_fmt(report.window_from) if report.window_from else '?'} to "
        f"{_fmt(report.window_to) if report.window_to else '?'}*",
        "",
        "## Layer B — exact aggregate reconciliation (tight)",
        "",
        f"- **Match rate:** {_pct(layer_b.match_rate)} "
        f"({layer_b.windows_matched}/{layer_b.windows_tested} windows) "
        f"within ±{layer_b.tolerance_gco2:g} gCO2/kWh",
        f"- **Largest mean difference:** {layer_b.max_abs_mean_difference} gCO2/kWh",
        "",
        "| Day | Mean (ours) | Avg (stats) | Diff | Min o/s | Max o/s | Match |",
        "|---|---|---|---|---|---|---|",
    ]
    for window in layer_b.windows:
        lines.append(
            f"| {window.label} | {window.recomputed_mean} | {window.stats_average} | "
            f"{window.mean_difference:+.2f} | {window.recomputed_min}/{window.stats_min} | "
            f"{window.recomputed_max}/{window.stats_max} | {'✓' if window.matched else '✗'} |"
        )

    lines += [
        "",
        "## Layer A — independent reconstruction (indicative)",
        "",
        f"- **Periods compared:** {layer_a.n_periods}",
        f"- **Mean difference (reconstructed − actual):** "
        f"{_signed(layer_a.mean_difference)} gCO2/kWh",
        f"- **Mean absolute difference:** {layer_a.mean_abs_difference} gCO2/kWh",
        f"- **Spread (std dev):** {layer_a.std_difference} gCO2/kWh",
        f"- **Range:** {_signed(layer_a.min_difference)} to "
        f"{_signed(layer_a.max_difference)} gCO2/kWh",
        f"- **Outliers flagged (>2σ from the mean gap):** {len(layer_a.outliers)}",
        "",
        f"> {layer_a.note}",
        "",
        "**Factor → mix mapping used (gCO2/kWh):** "
        + ", ".join(f"{fuel}={value:g}" for fuel, value in sorted(layer_a.factor_mapping.items())),
        "",
        f"*{report.attribution}*",
        "",
    ]
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"
