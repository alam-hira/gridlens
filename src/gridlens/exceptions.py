"""Custom exception hierarchy.

Principle: fail loud, never fabricate. Every failure mode raises a specific,
named error so callers can handle it explicitly instead of silently producing
a wrong number.
"""

from __future__ import annotations


class GridLensError(Exception):
    """Base class for every error raised by GridLens."""


class ConfigError(GridLensError):
    """Configuration or a region profile is missing or invalid."""


class DataSourceError(GridLensError):
    """The upstream data source could not be reached or returned an error."""


class DataValidationError(GridLensError):
    """Upstream data did not match the expected schema."""


class MetricError(GridLensError):
    """A metric could not be computed from the available data."""
