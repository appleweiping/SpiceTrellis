"""Read rectangular SPICE raw artifacts without executing any embedded commands."""

from __future__ import annotations

import hashlib
import math
import re
import struct
from pathlib import Path

from spicetrellis._result_numbers import decimal_sample
from spicetrellis.simulation_results import (
    CANONICAL_RAW_FIELDS,
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
)

_COUNT = re.compile(r"[0-9]{1,9}\Z")
_DEFAULT_LIMITS = ResultLimits()


class _Cursor:
    def __init__(self, payload: bytes, limits: ResultLimits) -> None:
        self.payload = payload
        self.offset = 0
        self.limits = limits

    def line(self) -> str:
        end = self.payload.find(b"\n", self.offset, self.offset + self.limits.max_line_bytes + 1)
        if end < 0:
            end = len(self.payload)
        if end - self.offset > self.limits.max_line_bytes:
            raise ResultError("raw line byte limit exceeded")
        content = self.payload[self.offset : end]
        self.offset = min(end + 1, len(self.payload))
        try:
            return content.decode("utf-8", errors="strict").strip(" \t\r")
        except UnicodeError as error:
            raise ResultError("raw header/value line is not valid UTF-8") from error

    def nonempty(self) -> str:
        while self.offset < len(self.payload):
            if line := self.line():
                return line
        raise ResultError("raw artifact is truncated")

    def has_data(self) -> bool:
        while self.offset < len(self.payload) and self.payload[self.offset] in b" \t\r\n":
            self.offset += 1
        return self.offset < len(self.payload)


def _count(value: str, where: str, maximum: int) -> int:
    if not _COUNT.fullmatch(value):
        raise ResultError(f"raw {where} must be a positive decimal integer")
    result = int(value)
    if not 1 <= result <= maximum:
        raise ResultError(f"raw {where} limit exceeded")
    return result


def _value(value: str, encoding: str) -> float | complex:
    if encoding == "real":
        return decimal_sample(value)
    components = value.split(",")
    if len(components) != 2:
        raise ResultError("raw complex values require one real,imaginary pair")
    return complex(decimal_sample(components[0].strip()), decimal_sample(components[1].strip()))


def _header(cursor: _Cursor) -> tuple[dict[str, str], int, int, str, tuple[int, ...]]:
    header: dict[str, str] = {}
    first = cursor.nonempty()
    if not first.startswith("Title:"):
        raise ResultError("each raw plot must begin with Title:")
    line = first
    while line != "Variables:":
        key, separator, value = line.partition(":")
        key, value = key.casefold(), value.strip()
        if not separator or not value or key in header or len(header) >= 128:
            raise ResultError("raw header contains malformed, duplicate or excessive fields")
        header[key] = value
        line = cursor.nonempty()
    required = {"title", "plotname", "flags", "no. variables", "no. points"}
    if required - header.keys():
        raise ResultError("raw header is missing required fields")
    flags = header["flags"].split()
    encodings = [flag for flag in flags if flag in {"real", "complex"}]
    if (
        len(encodings) != 1
        or len(flags) != len(set(flags))
        or set(flags)
        - {
            "real",
            "complex",
            "padded",
        }
    ):
        raise ResultError("raw flags require real/complex rectangular data; layout is unsupported")
    variables = _count(header["no. variables"], "variable count", cursor.limits.max_variables)
    points = _count(header["no. points"], "point count", cursor.limits.max_points)
    if points * variables > cursor.limits.max_cells:
        raise ResultError("raw cell limit exceeded")
    dimensions: tuple[int, ...] = (points,)
    if "dimensions" in header:
        items = header["dimensions"].split(",")
        if len(items) > 16:
            raise ResultError("raw Dimensions rank limit exceeded")
        dimensions = tuple(_count(item.strip(), "Dimensions", points) for item in items)
        if math.prod(dimensions) != points:
            raise ResultError("raw Dimensions must multiply to the point count")
    return header, variables, points, encodings[0], dimensions


def _variables(cursor: _Cursor, count: int) -> tuple[ResultVariable, ...]:
    variables: list[ResultVariable] = []
    names: set[str] = set()
    for index in range(count):
        fields = cursor.nonempty().split()
        if not 3 <= len(fields) <= 4 or fields[0] != str(index):
            raise ResultError("raw variable index or vector-specific metadata is unsupported")
        grid = None
        if len(fields) == 4:
            match = re.fullmatch(r"grid=([0-9]{1,3})", fields[3])
            if match is None:
                raise ResultError("raw vector metadata is unsupported")
            grid = int(match[1])
        variable = ResultVariable(fields[1], fields[2], grid)
        if variable.name.casefold() in names:
            raise ResultError("raw variable names must be unique ignoring case")
        names.add(variable.name.casefold())
        variables.append(variable)
    return tuple(variables)


def _columns(
    cursor: _Cursor,
    variables: int,
    points: int,
    encoding: str,
    byte_order: str | None,
) -> tuple[tuple[float | complex, ...], ...]:
    marker = cursor.nonempty()
    columns: list[list[float | complex]] = [[] for _ in range(variables)]
    if marker == "Values:":
        for point in range(points):
            first = cursor.nonempty().split(maxsplit=1)
            if len(first) != 2 or first[0] != str(point):
                raise ResultError("raw point index is missing or out of sequence")
            columns[0].append(_value(first[1], encoding))
            for index in range(1, variables):
                columns[index].append(_value(cursor.nonempty(), encoding))
    elif marker == "Binary:":
        if byte_order is None:
            raise ResultError("binary raw input requires explicit byte_order='little' or 'big'")
        decoder = struct.Struct(
            ("<" if byte_order == "little" else ">") + ("d" if encoding == "real" else "dd")
        )
        required = decoder.size * variables * points
        if required > len(cursor.payload) - cursor.offset:
            raise ResultError("binary raw artifact is truncated")
        for _ in range(points):
            for column in columns:
                parts = decoder.unpack_from(cursor.payload, cursor.offset)
                cursor.offset += decoder.size
                if any(not math.isfinite(part) for part in parts):
                    raise ResultError("binary raw values must be finite")
                column.append(parts[0] if encoding == "real" else complex(parts[0], parts[1]))
    else:
        raise ResultError("raw data must begin with Values: or Binary:")
    return tuple(tuple(column) for column in columns)


def parse_raw(
    payload: bytes, *, byte_order: str | None = None, limits: ResultLimits = _DEFAULT_LIMITS
) -> SimulationResult:
    """Parse appended ASCII/native-binary rectangular plots, preserving vector order.

    Binary raw files carry no endian tag. The caller must supply the producing
    machine's byte order; there is deliberately no heuristic or host-native guess.
    Unequal vector dimensions, unpadded and fast-access layouts are rejected.
    """
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    if not isinstance(payload, bytes):
        raise ResultError("raw payload must be bytes")
    if len(payload) > limits.max_bytes:
        raise ResultError("raw artifact byte limit exceeded")
    if byte_order is not None and (
        not isinstance(byte_order, str) or byte_order not in {"little", "big"}
    ):
        raise ResultError("byte_order must be little, big or None")
    cursor = _Cursor(payload, limits)
    plots: list[ResultPlot] = []
    total_cells = total_points = total_variables = 0
    while cursor.has_data():
        if len(plots) >= limits.max_plots:
            raise ResultError("raw plot count limit exceeded")
        header, variable_count, points, encoding, dimensions = _header(cursor)
        total_cells += variable_count * points
        total_points += points
        total_variables += variable_count
        if (
            total_cells > limits.max_cells
            or total_points > limits.max_points
            or total_variables > limits.max_variables
        ):
            raise ResultError("complete raw result cell/point/variable limit exceeded")
        variables = _variables(cursor, variable_count)
        columns = _columns(cursor, variable_count, points, encoding, byte_order)
        plots.append(
            ResultPlot(
                title=header["title"],
                analysis=header["plotname"],
                encoding=encoding,
                variables=variables,
                columns=columns,
                dimensions=dimensions,
                metadata=tuple(
                    (key, value) for key, value in header.items() if key not in CANONICAL_RAW_FIELDS
                ),
            )
        )
    return SimulationResult("spice-raw", hashlib.sha256(payload).hexdigest(), tuple(plots))


def load_raw(
    path: str | Path, *, byte_order: str | None = None, limits: ResultLimits = _DEFAULT_LIMITS
) -> SimulationResult:
    """Read at most the byte ceiling plus one before parsing one local artifact."""
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(limits.max_bytes + 1)
    except OSError as error:
        raise ResultError(f"cannot read raw artifact: {error}") from error
    return parse_raw(payload, byte_order=byte_order, limits=limits)
