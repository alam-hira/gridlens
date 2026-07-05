"""API tests via FastAPI TestClient, offline via a fake client (§11, §15)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gridlens.api import app, get_client, get_now
from gridlens.exceptions import DataSourceError, GridLensError

from .conftest import SAMPLE_NOW, FakeClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Override the HTTP-client dependency so the API computes from fixtures, never
    # the live network, and pin the clock so the window clamp (and thus the period
    # counts) is deterministic regardless of the day the test runs.
    app.dependency_overrides[get_client] = FakeClient
    app.dependency_overrides[get_now] = lambda: SAMPLE_NOW
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_returns_computed_json(client: TestClient) -> None:
    response = client.get("/metrics", params={"profile": "gb", "days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["intensity"]["mean"] is not None
    assert body["mix"]["renewable_share"] is not None
    assert len(body["trend"]) == body["n_periods"]


def test_validation_endpoint(client: TestClient) -> None:
    response = client.get("/validation")
    assert response.status_code == 200
    body = response.json()
    assert body["layer_b"]["match_rate"] == 1.0
    assert body["layer_a"]["n_periods"] == 336  # clamped to 7 complete days


def test_anomalies_endpoint(client: TestClient) -> None:
    response = client.get("/anomalies")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_dashboard_endpoint_serves_html(client: TestClient) -> None:
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.text.lstrip().startswith("<!doctype html>")


def test_bad_days_param_is_422(client: TestClient) -> None:
    response = client.get("/metrics", params={"days": 999})
    assert response.status_code == 422  # FastAPI validation


def test_regional_profile_maps_to_404(client: TestClient) -> None:
    response = client.get("/metrics", params={"profile": "scotland"})
    assert response.status_code == 404  # ConfigError → 404


def _raising_client(exc: Exception) -> Callable[[], Any]:
    """A get_client override whose every fetch raises ``exc``."""

    class _Client:
        def intensity(self, *a: Any, **k: Any) -> Any:
            raise exc

        def generation(self, *a: Any, **k: Any) -> Any:
            raise exc

        def stats(self, *a: Any, **k: Any) -> Any:
            raise exc

        def factors(self, *a: Any, **k: Any) -> Any:
            raise exc

    return _Client


def test_upstream_failure_maps_to_502() -> None:
    app.dependency_overrides[get_client] = _raising_client(DataSourceError("upstream down"))
    try:
        assert TestClient(app).get("/metrics").status_code == 502
    finally:
        app.dependency_overrides.clear()


def test_generic_engine_error_maps_to_500() -> None:
    app.dependency_overrides[get_client] = _raising_client(GridLensError("boom"))
    try:
        assert TestClient(app).get("/metrics").status_code == 500
    finally:
        app.dependency_overrides.clear()
