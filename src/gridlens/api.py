"""FastAPI service — exposes the engine's results as JSON (build-plan §11).

Thin by design: every data endpoint calls the same ``engine.build_report`` the
CLI and dashboard use, then returns the typed result — so the JSON here can never
disagree with the dashboard. Interactive OpenAPI docs are auto-served at ``/docs``.

The HTTP client is provided by a dependency (:func:`get_client`) so tests can
override it with a fixture-backed fake and never touch the network. Engine
exceptions are mapped to the right HTTP status: a bad/unavailable profile → 404,
an upstream failure or malformed upstream data → 502, other engine errors → 500.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Query
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .anomalies import Anomaly
from .client import CarbonIntensityClient
from .config import Settings
from .engine import build_report
from .exceptions import (
    ConfigError,
    DataSourceError,
    DataValidationError,
    GridLensError,
)
from .models import MetricsReport
from .render import build_dashboard
from .validation import ValidationReport

app = FastAPI(
    title="GridLens API",
    version=__version__,
    description="Trustworthy GB electricity analytics — every number computed in the engine.",
)

# Shared query parameters (documented once, validated by FastAPI → 422 on bad input).
ProfileQuery = Query("gb", description="Region profile to use.")
DaysQuery = Query(7, ge=1, le=14, description="Window length in days.")


def get_settings() -> Settings:
    """Provide runtime settings (overridable in tests)."""
    return Settings()


def get_client(settings: Settings = Depends(get_settings)) -> CarbonIntensityClient:
    """Provide the HTTP client (overridden with a fake in tests)."""
    return CarbonIntensityClient(settings)


def get_now() -> datetime:
    """Provide the current time as a dependency, so tests can pin the clock and
    the window computation (and thus the clamped period counts) stays deterministic."""
    return datetime.now(UTC)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@app.get("/metrics", response_model=MetricsReport)
def metrics(
    profile: str = ProfileQuery,
    days: int = DaysQuery,
    client: CarbonIntensityClient = Depends(get_client),
    now: datetime = Depends(get_now),
) -> MetricsReport:
    """Computed metrics for a profile and window."""
    return build_report(profile, days, client=client, now=now).metrics


@app.get("/anomalies", response_model=list[Anomaly])
def anomalies(
    profile: str = ProfileQuery,
    days: int = DaysQuery,
    client: CarbonIntensityClient = Depends(get_client),
    now: datetime = Depends(get_now),
) -> list[Anomaly]:
    """Deterministic anomaly flags for a profile and window."""
    return build_report(profile, days, client=client, now=now).anomalies


@app.get("/validation", response_model=ValidationReport)
def validation(
    profile: str = ProfileQuery,
    days: int = DaysQuery,
    client: CarbonIntensityClient = Depends(get_client),
    now: datetime = Depends(get_now),
) -> ValidationReport:
    """Two-layer validation report for a profile and window."""
    return build_report(profile, days, client=client, now=now).validation


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    profile: str = ProfileQuery,
    days: int = DaysQuery,
    client: CarbonIntensityClient = Depends(get_client),
    now: datetime = Depends(get_now),
) -> HTMLResponse:
    """Rendered self-contained HTML dashboard (convenience endpoint)."""
    report = build_report(profile, days, client=client, now=now)
    return HTMLResponse(content=build_dashboard(report))


# --- Exception → HTTP status mapping (fail loud, with the right code) --------


def _error(status_code: int, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(ConfigError)
def _handle_config(request: Request, exc: ConfigError) -> JSONResponse:
    return _error(404, exc)  # unknown / unavailable profile


@app.exception_handler(DataSourceError)
def _handle_source(request: Request, exc: DataSourceError) -> JSONResponse:
    return _error(502, exc)  # upstream unreachable


@app.exception_handler(DataValidationError)
def _handle_validation(request: Request, exc: DataValidationError) -> JSONResponse:
    return _error(502, exc)  # upstream sent something unexpected


@app.exception_handler(GridLensError)
def _handle_generic(request: Request, exc: GridLensError) -> JSONResponse:
    return _error(500, exc)  # any other engine failure
