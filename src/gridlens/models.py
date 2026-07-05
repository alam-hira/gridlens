"""Typed data models and boundary parsing.

Every response from the API is parsed into a Pydantic model. Validation happens
at the boundary, so malformed upstream data fails loud here (as a
``DataValidationError``) rather than flowing downstream as a silent wrong value.

The module is split into three groups:

* **API response shapes** — mirror the JSON the Carbon Intensity API returns.
* **Boundary parsers** — turn a raw ``dict`` payload into validated models,
  raising :class:`DataValidationError` on anything unexpected.
* **Computed result shapes** — the typed outputs the engine produces (metrics),
  reused verbatim by the CLI, the API (as JSON), and the dashboard renderer so
  there is a single source of truth for every number.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .exceptions import DataValidationError


class _Base(BaseModel):
    # ``populate_by_name`` lets us alias the API's ``from``/``to``/``max``/``min``
    # (reserved words / shadowing built-ins) to safe attribute names, while still
    # accepting the wire names. ``extra="ignore"`` means new upstream fields never
    # break parsing — we only assert on the fields we depend on.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# --- API response shapes -------------------------------------------------


class IntensityValue(_Base):
    """The ``intensity`` object inside an /intensity item."""

    forecast: int | None = None
    actual: int | None = None
    index: str | None = None


class IntensityPeriod(_Base):
    """One half-hourly settlement period of carbon intensity."""

    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    intensity: IntensityValue


def effective_intensity(value: IntensityValue) -> int | None:
    """The usable intensity for a period: ``actual`` where present, else ``forecast``.

    Recent/future half-hours carry a forecast but a null actual. This is the
    *single* definition of that fallback policy — metrics, validation, and
    anomalies all call it, so the rule can never drift between them. Returns
    ``None`` when neither is available, so callers drop the period rather than
    invent a zero.
    """
    if value.actual is not None:
        return value.actual
    return value.forecast


class GenerationFuel(_Base):
    """One fuel's share of the generation mix in a period."""

    fuel: str
    perc: float


class GenerationPeriod(_Base):
    """One half-hourly settlement period of the generation mix."""

    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    generationmix: list[GenerationFuel]


class StatsValue(_Base):
    """The ``intensity`` object inside an /intensity/stats block."""

    maximum: int = Field(alias="max")
    average: int
    minimum: int = Field(alias="min")
    index: str | None = None


class StatsPeriod(_Base):
    """One statistics block (the API's own max/avg/min over a sub-window)."""

    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    intensity: StatsValue


# --- Computed result shapes ---------------------------------------------
#
# These are what the engine *produces*. They are plain typed containers with no
# behaviour, so they serialise straight to JSON for the API and feed directly
# into the dashboard template.


class IntensityMetrics(_Base):
    """Summary statistics for carbon intensity over the window."""

    mean: float | None = None
    minimum: int | None = None
    maximum: int | None = None
    cleanest_at: datetime | None = None
    dirtiest_at: datetime | None = None
    n_periods: int = 0
    # How many half-hours fell back to ``forecast`` because ``actual`` was null.
    # Surfaced so the dashboard can label "actual vs forecast" honestly.
    n_forecast_used: int = 0
    # Share of half-hours in each intensity index band (e.g. {"low": 0.4, ...}).
    index_distribution: dict[str, float] = Field(default_factory=dict)


class FuelShare(_Base):
    """A single fuel's window-average share, used for the ranked mix."""

    fuel: str
    share: float


class MixMetrics(_Base):
    """Window-average generation mix and derived shares."""

    shares: dict[str, float] = Field(default_factory=dict)
    ranked: list[FuelShare] = Field(default_factory=list)
    renewable_share: float | None = None
    low_carbon_share: float | None = None
    fossil_share: float | None = None
    imports_share: float | None = None
    other_share: float | None = None


class Delta(_Base):
    """A period-over-period change for one metric (absolute and percentage)."""

    metric: str
    current: float | None = None
    previous: float | None = None
    absolute: float | None = None
    percent: float | None = None


class Records(_Base):
    """Notable single-period extremes within the window."""

    lowest_intensity: int | None = None
    lowest_intensity_at: datetime | None = None
    highest_intensity: int | None = None
    highest_intensity_at: datetime | None = None
    highest_renewable_share: float | None = None
    highest_renewable_at: datetime | None = None


class TrendPoint(_Base):
    """One point on the intensity trend line (drives the trend chart)."""

    at: datetime
    intensity: int | None = None
    is_forecast: bool = False


class TimeOfDayPoint(_Base):
    """Mean/min/max intensity for one local half-hour-of-day slot (48 per day)."""

    slot: str  # local "HH:MM" (Europe/London)
    mean: float | None = None
    minimum: int | None = None
    maximum: int | None = None


class MixOverTimePoint(_Base):
    """Hourly-averaged generation mix at one chronological point in the window."""

    at: datetime
    shares: dict[str, float] = Field(default_factory=dict)


class ScatterPoint(_Base):
    """One half-hour paired for the renewable-share vs carbon-intensity scatter."""

    renewable: float
    intensity: int


class DailyPoint(_Base):
    """One calendar day's rollup — the series behind the KPI sparklines."""

    day: date
    # How many half-hours this UTC day holds. A complete day is 48; a smaller
    # count means a partial day (typically today, still in progress), which the
    # dashboard labels "(partial)".
    n_periods: int = 0
    mean_intensity: float | None = None
    min_intensity: int | None = None
    max_intensity: int | None = None
    renewable_share: float | None = None
    low_carbon_share: float | None = None


class MetricsReport(_Base):
    """The full deterministic metric bundle for a window.

    This is the single object the API serves under ``/metrics`` and the object
    the dashboard renders from — so the numbers on the page and the numbers in
    the JSON are guaranteed identical.
    """

    window_from: datetime | None = None
    window_to: datetime | None = None
    n_periods: int = 0
    intensity: IntensityMetrics = Field(default_factory=IntensityMetrics)
    mix: MixMetrics = Field(default_factory=MixMetrics)
    records: Records = Field(default_factory=Records)
    comparison: list[Delta] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
    time_of_day: list[TimeOfDayPoint] = Field(default_factory=list)
    mix_over_time: list[MixOverTimePoint] = Field(default_factory=list)
    scatter: list[ScatterPoint] = Field(default_factory=list)
    renewable_intensity_r: float | None = None


# --- Boundary parsers ----------------------------------------------------


def _rows(payload: dict[str, Any], what: str) -> list[Any]:
    """Return the ``data`` list from a payload.

    The API is inconsistent: the current-snapshot endpoints (``/intensity``,
    ``/generation``) return a single object under ``data``, while the range
    endpoints return a list. We normalise both to a list so callers never have
    to care which endpoint produced the payload.
    """
    try:
        data = payload["data"]
    except (KeyError, TypeError) as exc:
        raise DataValidationError(f"Unexpected {what} payload (no 'data'): {exc}") from exc
    return data if isinstance(data, list) else [data]


def parse_intensity(payload: dict[str, Any]) -> list[IntensityPeriod]:
    """Parse an /intensity payload, raising DataValidationError on bad shape."""
    try:
        return [IntensityPeriod.model_validate(item) for item in _rows(payload, "intensity")]
    except (KeyError, TypeError, ValidationError) as exc:
        raise DataValidationError(f"Unexpected intensity payload: {exc}") from exc


def parse_generation(payload: dict[str, Any]) -> list[GenerationPeriod]:
    """Parse a /generation payload, raising DataValidationError on bad shape."""
    try:
        return [GenerationPeriod.model_validate(item) for item in _rows(payload, "generation")]
    except (KeyError, TypeError, ValidationError) as exc:
        raise DataValidationError(f"Unexpected generation payload: {exc}") from exc


def parse_stats(payload: dict[str, Any]) -> list[StatsPeriod]:
    """Parse an /intensity/stats payload, raising DataValidationError on bad shape."""
    try:
        return [StatsPeriod.model_validate(item) for item in _rows(payload, "stats")]
    except (KeyError, TypeError, ValidationError) as exc:
        raise DataValidationError(f"Unexpected stats payload: {exc}") from exc


def parse_factors(payload: dict[str, Any]) -> dict[str, int]:
    """Parse an /intensity/factors payload into a ``fuel -> gCO2/kWh`` mapping.

    The factors endpoint returns ``{"data": [ { "Gas (Combined Cycle)": 394, ...} ]}``
    — a single-element list wrapping one flat object of granular, Title-Cased
    fuel names. We validate it is exactly that and coerce the values to ``int``,
    failing loud on anything else. These are the *definitions* the engine uses
    for Layer A reconstruction (§6 principle: definitions are fetched, not
    invented).
    """
    rows = _rows(payload, "factors")
    if not rows or not isinstance(rows[0], dict):
        raise DataValidationError("Unexpected factors payload: no factor object found")
    try:
        return {str(name): int(value) for name, value in rows[0].items()}
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"Unexpected factors payload: {exc}") from exc
