"""Settings and region profiles.

Settings come from the environment (the standard 12-factor approach via
``pydantic-settings``). Region profiles are validated YAML files: adding a new
region is a new file, not a code change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigError


class Settings(BaseSettings):
    """Runtime settings, overridable via GRIDLENS_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="GRIDLENS_")

    base_url: str = "https://api.carbonintensity.org.uk"
    request_timeout: float = 10.0
    cache_ttl_seconds: int = 1800
    max_retries: int = 3

    # Anomaly thresholds (build-plan §9). Kept in config, not hardcoded in the
    # rules, so tuning a flag is a settings change (GRIDLENS_ANOMALY_*), not code.
    anomaly_deviation_pct: float = 15.0
    anomaly_swing_pp: float = 15.0


class Profile(BaseModel):
    """A 'view' of the grid: national or a specific region."""

    name: str
    scope: Literal["national", "regional"]
    region_id: int | None = None
    endpoints: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)


def _profiles_dir() -> Path:
    return Path(__file__).parent / "profiles"


def list_profiles(profiles_dir: Path | None = None) -> list[str]:
    """List the available profile names."""
    directory = profiles_dir or _profiles_dir()
    return sorted(path.stem for path in directory.glob("*.yaml"))


def load_profile(name: str, profiles_dir: Path | None = None) -> Profile:
    """Load and validate a region profile by name."""
    directory = profiles_dir or _profiles_dir()
    path = directory / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"No profile named {name!r} in {directory}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Profile.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"Invalid profile {name!r}: {exc}") from exc
