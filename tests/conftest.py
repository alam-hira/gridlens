from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gridlens.engine import DashboardReport, build_report
from gridlens.models import (
    parse_factors,
    parse_generation,
    parse_intensity,
    parse_stats,
)

FIXTURES = Path(__file__).parent / "fixtures"

# A fixed "now" aligned to the seven complete days in the fixtures.
SAMPLE_NOW = datetime(2026, 7, 3, tzinfo=UTC)


def load_fixture(name: str) -> dict[str, Any]:
    """Load a captured API fixture by filename (tests never hit the network)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    """A stand-in for CarbonIntensityClient that serves the captured fixtures.

    It ignores the requested window and returns the same seven-day fixtures, so
    the whole engine can be exercised deterministically and offline. The optional
    ``settings`` argument mirrors the real client's constructor so it can be
    monkeypatched in as a drop-in replacement.
    """

    def __init__(self, settings: object | None = None) -> None:
        self.settings = settings

    def intensity(self, from_iso: str, to_iso: str) -> list:
        return parse_intensity(load_fixture("intensity_range.json"))

    def generation(self, from_iso: str, to_iso: str) -> list:
        return parse_generation(load_fixture("generation_range.json"))

    def stats(self, from_iso: str, to_iso: str, block_hours: int | None = None) -> list:
        return parse_stats(load_fixture("stats_range.json"))

    def factors(self) -> dict:
        return parse_factors(load_fixture("factors.json"))


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def sample_report(fake_client: FakeClient) -> DashboardReport:
    """A fully-computed report built offline from the fixtures."""
    return build_report("gb", days=7, client=fake_client, now=SAMPLE_NOW)  # type: ignore[arg-type]


@pytest.fixture
def generation_good() -> dict[str, Any]:
    return load_fixture("generation_good.json")


@pytest.fixture
def generation_bad() -> dict[str, Any]:
    return load_fixture("generation_bad.json")


@pytest.fixture
def intensity_date() -> dict[str, Any]:
    """One day of half-hourly intensity; ``actual`` is null past mid-afternoon."""
    return load_fixture("intensity_date.json")


@pytest.fixture
def intensity_range() -> dict[str, Any]:
    """Seven complete days of half-hourly intensity (all ``actual`` present)."""
    return load_fixture("intensity_range.json")


@pytest.fixture
def stats_range() -> dict[str, Any]:
    """Seven daily statistics blocks matching ``intensity_range``."""
    return load_fixture("stats_range.json")


@pytest.fixture
def generation_range() -> dict[str, Any]:
    """Seven complete days of half-hourly generation mix."""
    return load_fixture("generation_range.json")


@pytest.fixture
def factors() -> dict[str, Any]:
    """The published per-fuel carbon factors (granular, Title-Cased names)."""
    return load_fixture("factors.json")
