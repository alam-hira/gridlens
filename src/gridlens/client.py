"""HTTP client for the Carbon Intensity API.

A real-world client: explicit timeouts so it never hangs, retry-with-backoff on
transient failures, on-disk response caching, and a single error type
(``DataSourceError``) for anything that goes wrong upstream.
"""

from __future__ import annotations

from typing import Any

import requests
import requests_cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .exceptions import DataSourceError
from .logging_config import get_logger
from .models import (
    GenerationPeriod,
    IntensityPeriod,
    StatsPeriod,
    parse_factors,
    parse_generation,
    parse_intensity,
    parse_stats,
)

logger = get_logger(__name__)


class CarbonIntensityClient:
    """Thin, resilient wrapper over the Carbon Intensity REST API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.session = requests_cache.CachedSession(
            cache_name="gridlens_cache",
            backend="sqlite",
            expire_after=self.settings.cache_ttl_seconds,
        )
        retry = Retry(
            total=self.settings.max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        logger.debug("GET %s", url)
        try:
            response = self.session.get(
                url,
                timeout=self.settings.request_timeout,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return payload
        except requests.RequestException as exc:
            raise DataSourceError(f"Failed to fetch {url}: {exc}") from exc

    def intensity(self, from_iso: str, to_iso: str) -> list[IntensityPeriod]:
        """Half-hourly carbon intensity over a window."""
        return parse_intensity(self._get(f"/intensity/{from_iso}/{to_iso}"))

    def generation(self, from_iso: str, to_iso: str) -> list[GenerationPeriod]:
        """Half-hourly generation mix over a window."""
        return parse_generation(self._get(f"/generation/{from_iso}/{to_iso}"))

    def stats(
        self, from_iso: str, to_iso: str, block_hours: int | None = None
    ) -> list[StatsPeriod]:
        """The API's own intensity statistics over a window.

        With ``block_hours`` the API splits the window into fixed blocks (e.g. 24
        for daily statistics), which is how Layer B gets a per-day figure to
        reconcile against.
        """
        path = f"/intensity/stats/{from_iso}/{to_iso}"
        if block_hours is not None:
            path = f"{path}/{block_hours}"
        return parse_stats(self._get(path))

    def factors(self) -> dict[str, int]:
        """The published per-fuel carbon factors (gCO2/kWh)."""
        return parse_factors(self._get("/intensity/factors"))
