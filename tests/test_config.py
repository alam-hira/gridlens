from __future__ import annotations

from pathlib import Path

import pytest

from gridlens.config import Profile, list_profiles, load_profile
from gridlens.exceptions import ConfigError


def test_load_bundled_profile() -> None:
    profile = load_profile("gb")
    assert profile.name == "Great Britain"
    assert profile.scope == "national"


def test_list_profiles_includes_bundled() -> None:
    assert "gb" in list_profiles()


def test_missing_profile_fails_loud() -> None:
    with pytest.raises(ConfigError):
        load_profile("does-not-exist")


def test_add_region_needs_no_code_change(tmp_path: Path) -> None:
    (tmp_path / "orkney.yaml").write_text(
        "name: Orkney\n"
        "scope: regional\n"
        "region_id: 13\n"
        "endpoints:\n"
        "  intensity: /regional/regionid\n"
        "labels:\n"
        "  title: Orkney electricity\n",
        encoding="utf-8",
    )
    profile = load_profile("orkney", profiles_dir=tmp_path)
    assert isinstance(profile, Profile)
    assert profile.region_id == 13
