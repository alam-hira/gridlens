from __future__ import annotations

from typing import Any

import pytest

from gridlens.exceptions import DataValidationError
from gridlens.models import parse_factors, parse_generation

from .conftest import load_fixture


def test_parse_generation_good(generation_good: dict[str, Any]) -> None:
    periods = parse_generation(generation_good)
    assert len(periods) == 1
    assert periods[0].generationmix[0].fuel == "gas"
    assert periods[0].generationmix[0].perc == 32.0


def test_parse_generation_bad_fails_loud(generation_bad: dict[str, Any]) -> None:
    with pytest.raises(DataValidationError):
        parse_generation(generation_bad)


def test_parse_generation_accepts_single_object() -> None:
    # The current /generation endpoint returns one object under "data", not a
    # list; the parser must normalise it to a single-element list.
    periods = parse_generation(load_fixture("generation.json"))
    assert len(periods) == 1
    assert {fuel.fuel for fuel in periods[0].generationmix} >= {"gas", "wind", "solar"}


def test_parse_factors_maps_titlecased_names(factors: dict[str, Any]) -> None:
    mapping = parse_factors(factors)
    # Granular, Title-Cased fuel names → integer gCO2/kWh.
    assert mapping["Gas (Combined Cycle)"] == 394
    assert mapping["Coal"] == 937
    assert mapping["Wind"] == 0


def test_parse_factors_fails_loud_on_bad_shape() -> None:
    with pytest.raises(DataValidationError):
        parse_factors({"data": []})
    with pytest.raises(DataValidationError):
        parse_factors({"nope": 1})
