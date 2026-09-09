"""Immutable, bounded simulation-result values independent of simulator processes."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

CANONICAL_RAW_FIELDS = frozenset(
    {"title", "plotname", "flags", "no. variables", "no. points", "dimensions"}
)


class ResultError(ValueError):
    """A simulator artifact is malformed, unsupported, or exceeds a work limit."""


def _text(value: object, where: str, *, maximum: int = 16_384) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)
    ):
        raise ResultError(f"{where} must be bounded, non-empty, normalized display-safe text")
    return value


def _token(value: object, where: str) -> str:
    result = _text(value, where, maximum=1_024)
    if any(char.isspace() for char in result):
        raise ResultError(f"{where} cannot contain whitespace")
    return result


@dataclass(frozen=True, slots=True)
class ResultLimits:
    """Caller-lowerable parsing ceilings; counts apply to the complete artifact."""

    max_bytes: int = 64 * 1024 * 1024
    max_plots: int = 128
    max_variables: int = 4_096
    max_points: int = 2_000_000
    max_cells: int = 2_000_000
    max_line_bytes: int = 65_536

    def __post_init__(self) -> None:
        for name, ceiling in (
            ("max_bytes", 256 * 1024 * 1024),
            ("max_plots", 4_096),
            ("max_variables", 16_384),
            ("max_points", 8_000_000),
            ("max_cells", 8_000_000),
            ("max_line_bytes", 1_048_576),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
                raise ResultError(f"{name} limit must be an integer in 1..{ceiling}")


@dataclass(frozen=True, slots=True)
class ResultVariable:
    """An exact simulator vector name and declared physical quantity (not an inferred unit)."""

    name: str
    quantity: str
    display_grid: int | None = None

    def __post_init__(self) -> None:
        _token(self.name, "variable name")
        _token(self.quantity, "variable quantity")
        if self.display_grid is not None and (
            type(self.display_grid) is not int or not 0 <= self.display_grid <= 255
        ):
            raise ResultError("variable display_grid must be a bounded integer or None")


@dataclass(frozen=True, slots=True)
class ResultPlot:
    """Column-major vectors in their original order, with explicit real/complex encoding."""

    title: str
    analysis: str
    encoding: str
    variables: tuple[ResultVariable, ...]
    columns: tuple[tuple[float | complex, ...], ...]
    dimensions: tuple[int, ...]
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.title, "plot title")
        _text(self.analysis, "plot analysis")
        if not isinstance(self.encoding, str) or self.encoding not in {"real", "complex"}:
            raise ResultError("plot encoding must be real or complex")
        if (
            not isinstance(self.variables, tuple)
            or not 1 <= len(self.variables) <= 16_384
            or any(not isinstance(variable, ResultVariable) for variable in self.variables)
        ):
            raise ResultError("plot variables must be a bounded non-empty tuple")
        names = [variable.name.casefold() for variable in self.variables]
        if len(names) != len(set(names)):
            raise ResultError("plot variable names must be unique ignoring case")
        if (
            not isinstance(self.columns, tuple)
            or len(self.columns) != len(self.variables)
            or any(not isinstance(column, tuple) for column in self.columns)
        ):
            raise ResultError("plot columns must match the variable tuple")
        points = len(self.columns[0])
        if (
            not 1 <= points <= 8_000_000
            or points * len(self.columns) > 8_000_000
            or any(len(column) != points for column in self.columns)
        ):
            raise ResultError("plot columns must have one bounded common length")
        expected_type = float if self.encoding == "real" else complex
        for column in self.columns:
            for value in column:
                if type(value) is not expected_type:
                    raise ResultError("plot value type does not match its encoding")
                if not math.isfinite(value.real) or not math.isfinite(value.imag):
                    raise ResultError("plot values must be finite")
        if (
            not isinstance(self.dimensions, tuple)
            or not 1 <= len(self.dimensions) <= 16
            or any(type(item) is not int or not 1 <= item <= points for item in self.dimensions)
            or math.prod(self.dimensions) != points
        ):
            raise ResultError("plot Dimensions must multiply to its point count")
        if not isinstance(self.metadata, tuple) or len(self.metadata) > 128:
            raise ResultError("plot metadata must be a bounded tuple")
        keys: set[str] = set()
        for pair in self.metadata:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ResultError("plot metadata entries must be key/value tuples")
            key, metadata_value = pair
            _text(key, "metadata key", maximum=128)
            _text(metadata_value, "metadata value")
            if key != key.strip():
                raise ResultError("metadata keys cannot have surrounding whitespace")
            if key.casefold() in CANONICAL_RAW_FIELDS:
                raise ResultError("metadata cannot duplicate canonical plot fields")
            if key.casefold() in keys:
                raise ResultError("duplicate plot metadata key")
            keys.add(key.casefold())

    @property
    def points(self) -> int:
        return len(self.columns[0])


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Result values carrying a source-digest claim, not proof of integrity or solver success.

    Raw/CSV parsers compute the digest from their input bytes. A constructor or
    JSON loader alone cannot verify the claim without the original source.
    """

    format: str
    source_sha256: str
    plots: tuple[ResultPlot, ...]

    def __post_init__(self) -> None:
        _token(self.format, "result format")
        if (
            not isinstance(self.source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
        ):
            raise ResultError("result source_sha256 must be a lowercase SHA-256 digest")
        if (
            not isinstance(self.plots, tuple)
            or not 1 <= len(self.plots) <= 4_096
            or any(not isinstance(plot, ResultPlot) for plot in self.plots)
        ):
            raise ResultError("result plots must be a bounded non-empty tuple")
        if sum(plot.points * len(plot.variables) for plot in self.plots) > 8_000_000:
            raise ResultError("complete result cell limit exceeded")
        if sum(len(plot.variables) for plot in self.plots) > 16_384:
            raise ResultError("complete result variable limit exceeded")
