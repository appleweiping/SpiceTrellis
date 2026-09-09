"""Versioned, strict JSON transport for process-independent simulation results."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from io import StringIO
from pathlib import Path
from typing import Any

from spicetrellis._output import write_text_atomic
from spicetrellis.simulation_results import (
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
)

RESULT_SCHEMA = "org.spicetrellis.simulation-result"
RESULT_VERSION = 1
_DEFAULT_LIMITS = ResultLimits()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResultError("simulation-result JSON contains duplicate object keys")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ResultError(f"simulation-result JSON contains non-finite constant {value}")


def _integer(value: str) -> int:
    if len(value) > 128:
        raise ResultError("simulation-result JSON integer length limit exceeded")
    return int(value)


def _float(value: str) -> float:
    if len(value) > 128:
        raise ResultError("simulation-result JSON number length limit exceeded")
    result = float(value)
    if not math.isfinite(result):
        raise ResultError("simulation-result JSON number must be finite")
    mantissa = value.lower().partition("e")[0]
    if result == 0 and any(character in "123456789" for character in mantissa):
        raise ResultError("simulation-result JSON number underflows binary64")
    return result


def _complexity(value: Any, limits: ResultLimits) -> None:
    maximum = limits.max_cells * 3 + limits.max_variables * 10 + limits.max_plots * 450 + 32
    stack: list[tuple[Iterator[Any], int]] = [(iter((value,)), 0)]
    count = 0
    while stack:
        iterator, depth = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        count += 1
        if count > maximum:
            raise ResultError("simulation-result JSON node limit exceeded")
        if depth > 32:
            raise ResultError("simulation-result JSON depth limit exceeded")
        if isinstance(item, dict):
            stack.append((iter(item.values()), depth + 1))
        elif isinstance(item, list):
            stack.append((iter(item), depth + 1))


def _object(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ResultError(f"{where} has missing or unknown fields")
    return value


def _array(value: Any, maximum: int, where: str, *, empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or not (0 if empty else 1) <= len(value) <= maximum:
        raise ResultError(f"{where} array limit or shape is invalid")
    return value


def _number(value: Any) -> float:
    if type(value) not in {int, float}:
        raise ResultError("result samples must be JSON numbers, not booleans or strings")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ResultError("result sample cannot be represented as binary64") from error
    if not math.isfinite(result):
        raise ResultError("result samples must be finite")
    return result


def _plot(value: Any, limits: ResultLimits) -> ResultPlot:
    body = _object(
        value,
        {"title", "analysis", "encoding", "variables", "columns", "dimensions", "metadata"},
        "plot",
    )
    encoding = body["encoding"]
    if not isinstance(encoding, str) or encoding not in {"real", "complex"}:
        raise ResultError("plot encoding must be real or complex")
    raw_variables = _array(body["variables"], limits.max_variables, "variables")
    variables = []
    for raw in raw_variables:
        item = _object(raw, {"name", "quantity", "display_grid"}, "variable")
        variables.append(ResultVariable(item["name"], item["quantity"], item["display_grid"]))
    raw_columns = _array(body["columns"], limits.max_variables, "columns")
    if len(raw_columns) != len(variables):
        raise ResultError("result columns must match variables")
    columns: list[tuple[float | complex, ...]] = []
    points = 0
    for raw_column in raw_columns:
        column = _array(raw_column, limits.max_points, "column")
        if points and len(column) != points:
            raise ResultError("result columns must have equal lengths")
        points = len(column)
        if points * len(variables) > limits.max_cells:
            raise ResultError("result cell limit exceeded")
        if encoding == "real":
            columns.append(tuple(_number(sample) for sample in column))
        else:
            samples: list[complex] = []
            for sample in column:
                pair = _array(sample, 2, "complex sample")
                if len(pair) != 2:
                    raise ResultError("complex sample requires exactly real and imaginary parts")
                samples.append(complex(_number(pair[0]), _number(pair[1])))
            columns.append(tuple(samples))
    metadata: list[tuple[str, str]] = []
    for pair in _array(body["metadata"], 128, "metadata", empty=True):
        checked = _array(pair, 2, "metadata entry")
        if len(checked) != 2:
            raise ResultError("metadata entry requires exactly key and value")
        metadata.append((checked[0], checked[1]))
    return ResultPlot(
        body["title"],
        body["analysis"],
        encoding,
        tuple(variables),
        tuple(columns),
        tuple(_array(body["dimensions"], 16, "dimensions")),
        tuple(metadata),
    )


def load_result_text(
    payload: str | bytes, *, limits: ResultLimits = _DEFAULT_LIMITS
) -> SimulationResult:
    """Read the exact v1 wire shape, rejecting unknown fields and malformed values."""
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    try:
        data = payload.encode("utf-8", errors="strict") if isinstance(payload, str) else payload
        if not isinstance(data, bytes):
            raise ResultError("simulation-result JSON must be str or bytes")
        if len(data) > limits.max_bytes:
            raise ResultError("simulation-result JSON byte limit exceeded")
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_int=_integer,
            parse_float=_float,
        )
    except ResultError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise ResultError("simulation-result JSON is malformed or exceeds parser limits") from error
    _complexity(value, limits)
    body = _object(
        value,
        {"schema", "schema_version", "format", "source_sha256", "plots"},
        "result",
    )
    if (
        body["schema"] != RESULT_SCHEMA
        or type(body["schema_version"]) is not int
        or body["schema_version"] != RESULT_VERSION
    ):
        raise ResultError("unsupported simulation-result schema or version")
    plots = []
    cells = points = variables = 0
    for item in _array(body["plots"], limits.max_plots, "plots"):
        plot = _plot(item, limits)
        cells += plot.points * len(plot.variables)
        points += plot.points
        variables += len(plot.variables)
        if (
            cells > limits.max_cells
            or points > limits.max_points
            or variables > limits.max_variables
        ):
            raise ResultError("complete simulation-result cell/point/variable limit exceeded")
        plots.append(plot)
    return SimulationResult(body["format"], body["source_sha256"], tuple(plots))


def _wire_chunks(value: Any) -> Iterator[str]:
    """Walk validated values lazily; never duplicate a plot's sample graph."""
    if isinstance(value, SimulationResult):
        value = {
            "schema": RESULT_SCHEMA,
            "schema_version": RESULT_VERSION,
            "format": value.format,
            "source_sha256": value.source_sha256,
            "plots": value.plots,
        }
    elif isinstance(value, ResultPlot):
        value = {
            "title": value.title,
            "analysis": value.analysis,
            "encoding": value.encoding,
            "variables": value.variables,
            "columns": value.columns,
            "dimensions": value.dimensions,
            "metadata": value.metadata,
        }
    elif isinstance(value, ResultVariable):
        value = {"name": value.name, "quantity": value.quantity, "display_grid": value.display_grid}
    elif isinstance(value, complex):
        value = (value.real, value.imag)
    if isinstance(value, dict):
        yield "{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=True)
            yield ":"
            yield from _wire_chunks(value[key])
        yield "}"
    elif isinstance(value, tuple):
        yield "["
        for index, child in enumerate(value):
            if index:
                yield ","
            yield from _wire_chunks(child)
        yield "]"
    else:
        yield json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def dump_result(result: SimulationResult, *, limits: ResultLimits = _DEFAULT_LIMITS) -> str:
    """Emit bounded deterministic JSON readable with the same limits; no signature is implied."""
    if not isinstance(result, SimulationResult):
        raise ResultError("result must be SimulationResult")
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    if (
        len(result.plots) > limits.max_plots
        or sum(len(plot.variables) for plot in result.plots) > limits.max_variables
        or sum(plot.points for plot in result.plots) > limits.max_points
        or sum(plot.points * len(plot.variables) for plot in result.plots) > limits.max_cells
    ):
        raise ResultError("complete simulation-result plot/variable/point/cell limit exceeded")
    output = StringIO()
    size = 1  # Include the final newline in the source-byte contract.
    for chunk in _wire_chunks(result):
        size += len(chunk)  # ensure_ascii makes the encoded character and byte counts equal.
        if size > limits.max_bytes:
            raise ResultError("simulation-result JSON byte limit exceeded")
        output.write(chunk)
    output.write("\n")
    return output.getvalue()


def load_result(path: str | Path, *, limits: ResultLimits = _DEFAULT_LIMITS) -> SimulationResult:
    """Read a bounded JSON artifact without changing any source file."""
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(limits.max_bytes + 1)
    except OSError as error:
        raise ResultError(f"cannot read simulation-result JSON: {error}") from error
    return load_result_text(payload, limits=limits)


def write_result(
    result: SimulationResult,
    destination: str | Path,
    *,
    force: bool = False,
    protected: Iterable[Path] = (),
    limits: ResultLimits = _DEFAULT_LIMITS,
) -> None:
    """Write atomically, refuse clobber by default, and always protect declared inputs."""
    try:
        write_text_atomic(
            destination, dump_result(result, limits=limits), force=force, protected=tuple(protected)
        )
    except (OSError, ValueError) as error:
        raise ResultError(f"cannot write simulation-result JSON: {error}") from error
