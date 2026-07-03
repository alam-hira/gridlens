"""Two-layer validation against the data source's own figures (build-plan §8).

This is the heart of the project: the claim "the numbers are trustworthy" is
*proven*, not asserted, by reconciling the engine's independent arithmetic
against the API's own published figures.

Layer B — **exact aggregate reconciliation** (rigorous, tight tolerance).
    Recompute the window's mean/min/max intensity from the half-hourly series and
    reconcile against ``/intensity/stats`` for the same window. Because both
    derive from the *same* series, the only expected difference is rounding, so
    the tolerance is tight (±1 gCO2/kWh). This proves the engine's arithmetic and
    date handling against a known-good reference.

Layer A — **independent intensity reconstruction** (honest, loose tolerance).
    Rebuild an *indicative* intensity from the generation mix and published
    factors: ``intensity ≈ Σ (fuel_fraction × factor)``. This deliberately will
    NOT match the official figure, because the official methodology adds
    interconnector-imports-by-source, transmission & distribution losses, and
    embedded wind/solar that a naive mix×factor sum cannot see. The point is to
    compute genuinely independently and then *quantify the gap honestly* — report
    its distribution and flag the periods where it is unusually large.
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean, pstdev

from pydantic import BaseModel, Field

from .models import GenerationPeriod, IntensityPeriod, StatsPeriod, effective_intensity

# ---------------------------------------------------------------------------
# The factor → mix-fuel mapping (THE Layer A modelling decision)
# ---------------------------------------------------------------------------
#
# The published factors use granular, Title-Cased fuel names; the generation mix
# uses coarse, lowercase ones. We must collapse the former onto the latter:
#
#   factors (granular)                     mix (coarse)
#   ------------------                     ------------
#   Biomass                          ->    biomass
#   Coal                             ->    coal
#   Gas (Combined Cycle) / (Open ..) ->    gas       <- ambiguous: see below
#   Nuclear                          ->    nuclear
#   Hydro                            ->    hydro
#   Solar                            ->    solar
#   Wind                             ->    wind
#   Other                            ->    other
#   Dutch / French / Irish Imports   ->    imports   <- ambiguous: see below
#
# Two factor names have no coarse counterpart and are intentionally *dropped* (not
# mapped): ``Oil`` and ``Pumped Storage``. Both are negligible in the GB mix and
# the coarse ``generationmix`` never reports them separately, so folding them in
# would attribute emissions to a share that is always zero — they are excluded
# from the reconstruction rather than forced into ``other``/``hydro``.
#
# Two coarse fuels have no unambiguous factor and force a decision:
#
#   * ``gas``: the factor table splits gas into Combined Cycle (394) and Open
#     Cycle (651). The mix does not say which. We use **Combined Cycle**, because
#     CCGT supplies the overwhelming majority of GB gas generation while OCGT is
#     a small peaking reserve. Trade-off: during peaking events this understates
#     gas intensity, contributing to Layer A's (expected) divergence.
#
#   * ``imports``: the factor table splits interconnectors by country (Dutch 474,
#     French 53, Irish 458, …) but the mix reports a single aggregate ``imports``
#     share with no source breakdown. We use the **simple mean of all
#     interconnector factors**. Trade-off: the true carbon content depends on the
#     live flow split (France is nuclear-heavy and very low; others higher), which
#     the coarse mix hides — this is the single biggest reason Layer A is only
#     *indicative*, exactly as the plan predicts, and we report it rather than
#     paper over it. (A future version could promote this mapping into profile
#     config, per build-plan §5/§6 "config-driven, not hardcoded".)
GAS_FACTOR_SOURCE = "Gas (Combined Cycle)"

# Direct 1:1 factor names for the coarse fuels that map cleanly.
_DIRECT_FACTOR_NAMES: dict[str, str] = {
    "biomass": "Biomass",
    "coal": "Coal",
    "nuclear": "Nuclear",
    "hydro": "Hydro",
    "solar": "Solar",
    "wind": "Wind",
    "other": "Other",
}


def map_factors_to_mix(factors: dict[str, int]) -> dict[str, float]:
    """Collapse the granular published factors onto the coarse mix fuel names.

    Returns a ``mix-fuel -> gCO2/kWh`` mapping. ``gas`` uses the CCGT factor and
    ``imports`` uses the mean of every interconnector factor, per the documented
    decisions above. A fuel whose source factor is missing is simply omitted, so
    the reconstruction excludes it rather than inventing a value.
    """
    mapping: dict[str, float] = {}
    for mix_fuel, factor_name in _DIRECT_FACTOR_NAMES.items():
        if factor_name in factors:
            mapping[mix_fuel] = float(factors[factor_name])

    if GAS_FACTOR_SOURCE in factors:
        mapping["gas"] = float(factors[GAS_FACTOR_SOURCE])

    import_factors = [value for name, value in factors.items() if "Imports" in name]
    if import_factors:
        mapping["imports"] = round(mean(import_factors), 2)

    return mapping


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class LayerBWindow(BaseModel):
    """One reconciled sub-window (typically a day)."""

    label: str
    recomputed_mean: float
    recomputed_min: int
    recomputed_max: int
    stats_average: int
    stats_min: int
    stats_max: int
    mean_difference: float
    matched: bool


class LayerBResult(BaseModel):
    """Layer B outcome: match rate across every reconciled window."""

    windows_tested: int = 0
    windows_matched: int = 0
    match_rate: float | None = None
    tolerance_gco2: float = 1.0
    max_abs_mean_difference: float | None = None
    windows: list[LayerBWindow] = Field(default_factory=list)


class LayerAOutlier(BaseModel):
    """A half-hour where the reconstruction diverges unusually far from actual."""

    at: datetime
    reconstructed: float
    actual: int
    difference: float


class LayerAResult(BaseModel):
    """Layer A outcome: the honest difference distribution, not a pass/fail."""

    n_periods: int = 0
    mean_difference: float | None = None
    mean_abs_difference: float | None = None
    std_difference: float | None = None
    min_difference: float | None = None
    max_difference: float | None = None
    outliers: list[LayerAOutlier] = Field(default_factory=list)
    factor_mapping: dict[str, float] = Field(default_factory=dict)
    note: str = ""


class ValidationReport(BaseModel):
    """The full two-layer validation result served by the CLI and API."""

    layer_b: LayerBResult = Field(default_factory=LayerBResult)
    layer_a: LayerAResult = Field(default_factory=LayerAResult)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer B — exact aggregate reconciliation
# ---------------------------------------------------------------------------


def _effective_actual(period: IntensityPeriod) -> int | None:
    """The value the stats endpoint aggregates: actual, else forecast."""
    return effective_intensity(period.intensity)


def reconcile_stats(
    periods: list[IntensityPeriod],
    stats: list[StatsPeriod],
    tolerance: float = 1.0,
) -> LayerBResult:
    """Layer B: reconcile our recomputed aggregates against ``/intensity/stats``.

    For each statistics block we gather the half-hourly periods that fall inside
    it, recompute mean/min/max from the same series the API used, and check all
    three agree within ``tolerance`` gCO2/kWh (the only legitimate difference is
    that the API rounds the mean to an integer). The result reports the match
    rate across *every* block — one matching example proves nothing (§8).
    """
    result = LayerBResult(tolerance_gco2=tolerance)
    max_abs = 0.0

    for block in stats:
        members = [
            value
            for period in periods
            if block.start <= period.start < block.end
            and (value := _effective_actual(period)) is not None
        ]
        if not members:
            continue

        recomputed_mean = mean(members)
        recomputed_min = min(members)
        recomputed_max = max(members)
        mean_diff = recomputed_mean - block.intensity.average
        matched = (
            abs(round(recomputed_mean) - block.intensity.average) <= tolerance
            and abs(recomputed_min - block.intensity.minimum) <= tolerance
            and abs(recomputed_max - block.intensity.maximum) <= tolerance
        )
        max_abs = max(max_abs, abs(mean_diff))

        result.windows.append(
            LayerBWindow(
                label=block.start.date().isoformat(),
                recomputed_mean=round(recomputed_mean, 2),
                recomputed_min=recomputed_min,
                recomputed_max=recomputed_max,
                stats_average=block.intensity.average,
                stats_min=block.intensity.minimum,
                stats_max=block.intensity.maximum,
                mean_difference=round(mean_diff, 2),
                matched=matched,
            )
        )

    result.windows_tested = len(result.windows)
    result.windows_matched = sum(1 for window in result.windows if window.matched)
    if result.windows_tested:
        result.match_rate = round(result.windows_matched / result.windows_tested, 4)
        result.max_abs_mean_difference = round(max_abs, 2)
    return result


# ---------------------------------------------------------------------------
# Layer A — independent intensity reconstruction
# ---------------------------------------------------------------------------

_LAYER_A_NOTE = (
    "Indicative only. Reconstructed as Σ(fuel_share × published_factor) using the "
    "coarse generation mix; it omits interconnector imports by source, transmission "
    "& distribution losses, and embedded (behind-meter) wind and solar, so a gap to "
    "the official figure is expected — the value is quantifying that gap, not matching it."
)


def reconstruct_intensity(
    generation: list[GenerationPeriod],
    factors: dict[str, int],
    actual: list[IntensityPeriod],
    outlier_sigma: float = 2.0,
) -> LayerAResult:
    """Layer A: reconstruct intensity from mix × factors and report the gap.

    Aligns each generation period with the reported intensity for the same
    half-hour, computes ``Σ(share/100 × mapped_factor)``, and summarises the
    signed differences (reconstructed − actual): the mean bias, the spread, and
    the periods more than ``outlier_sigma`` standard deviations from that bias.
    """
    mapping = map_factors_to_mix(factors)
    actual_by_start = {
        period.start: value for period in actual if (value := _effective_actual(period)) is not None
    }

    differences: list[tuple[datetime, float, int, float]] = []
    for period in generation:
        reported = actual_by_start.get(period.start)
        if reported is None:
            continue
        reconstructed = sum(
            fuel.perc / 100.0 * mapping[fuel.fuel]
            for fuel in period.generationmix
            if fuel.fuel in mapping
        )
        differences.append((period.start, reconstructed, reported, reconstructed - reported))

    result = LayerAResult(factor_mapping=mapping, note=_LAYER_A_NOTE)
    if not differences:
        return result

    diffs = [difference for _at, _recon, _actual, difference in differences]
    result.n_periods = len(diffs)
    result.mean_difference = round(mean(diffs), 2)
    result.mean_abs_difference = round(mean(abs(d) for d in diffs), 2)
    result.std_difference = round(pstdev(diffs), 2) if len(diffs) > 1 else 0.0
    result.min_difference = round(min(diffs), 2)
    result.max_difference = round(max(diffs), 2)

    # Flag periods whose gap is unusually far from the *typical* (mean) gap.
    threshold = outlier_sigma * (result.std_difference or 0.0)
    outliers = [
        LayerAOutlier(
            at=at,
            reconstructed=round(reconstructed, 2),
            actual=reported,
            difference=round(difference, 2),
        )
        for (at, reconstructed, reported, difference) in differences
        if threshold and abs(difference - result.mean_difference) > threshold
    ]
    result.outliers = sorted(outliers, key=lambda o: abs(o.difference), reverse=True)[:10]
    return result


def validate(
    intensity: list[IntensityPeriod],
    stats: list[StatsPeriod],
    generation: list[GenerationPeriod],
    factors: dict[str, int],
    tolerance: float = 1.0,
) -> ValidationReport:
    """Run both layers and bundle them into a single report."""
    layer_b = reconcile_stats(intensity, stats, tolerance=tolerance)
    layer_a = reconstruct_intensity(generation, factors, intensity)
    notes = [
        f"Layer B reconciles recomputed mean/min/max to /intensity/stats within "
        f"±{tolerance:g} gCO2/kWh across {layer_b.windows_tested} window(s).",
        _LAYER_A_NOTE,
    ]
    return ValidationReport(layer_b=layer_b, layer_a=layer_a, notes=notes)
