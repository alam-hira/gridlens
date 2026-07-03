"""Client fail-loud tests — the one core guarantee the fake client can't cover.

These exercise the real ``CarbonIntensityClient._get`` (timeout + error
translation) without hitting the network, by monkeypatching the session's
``get``. Build-plan §14.5 makes "a forced error path raises clearly" an explicit
acceptance check, and "fail loud, never fabricate" is a guiding principle.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from gridlens.client import CarbonIntensityClient
from gridlens.config import Settings
from gridlens.exceptions import DataSourceError


def test_upstream_failure_raises_data_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CarbonIntensityClient(Settings(max_retries=0))

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(client.session, "get", boom)
    with pytest.raises(DataSourceError):
        client.intensity("2026-06-01T00:00Z", "2026-06-02T00:00Z")


def test_get_passes_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CarbonIntensityClient(Settings(request_timeout=3.5))
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"data": []}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(client.session, "get", fake_get)
    client.stats("2026-06-01T00:00Z", "2026-06-02T00:00Z")
    # Every outbound request must carry a timeout so the client can never hang.
    assert captured["timeout"] == 3.5
