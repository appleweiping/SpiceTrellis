"""Read Xyce's unquoted CSV print profile without guessing column or step semantics."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from spicetrellis._result_numbers import decimal_sample
from spicetrellis.simulation_results import (
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
    _text,
)

_DEFAULT_LIMITS = ResultLimits()


def _header(line: str, limits: ResultLimits) -> tuple[ResultVariable, ...]:
    # Xyce prints names such as Re(V(a,b)) without CSV quoting. Only a comma
    # outside an expression's balanced delimiters separates result columns.
    stack: list[str] = []
    names = []
    start = 0
    matching = {"(": ")", "{": "}"}
    for index, char in enumerate(line):
        if char in "\"'\\[]":
            raise ResultError("Xyce CSV quoted, escaped or bracketed header is unsupported")
        if char in matching:
            stack.append(matching[char])
            if len(stack) > 32:
                raise ResultError("Xyce CSV header nesting limit exceeded")
        elif char in ")}":
            if not stack or stack.pop() != char:
                raise ResultError("Xyce CSV header delimiters are unbalanced")
        elif char == "," and not stack:
            names.append(line[start:index].strip())
            start = index + 1
            if len(names) >= limits.max_variables:
                raise ResultError("Xyce CSV variable limit exceeded")
    if stack:
        raise ResultError("Xyce CSV header delimiters are unbalanced")
    names.append(line[start:].strip())
    if len(names) > limits.max_variables:
        raise ResultError("Xyce CSV variable limit exceeded")
    if len(names) != len({name.casefold() for name in names}):
        raise ResultError("Xyce CSV variable names must be unique ignoring case")
    # A CSV label is not a physical-unit declaration. Even VR/VP expressions
    # retain an unknown quantity until a separate request contract supplies it.
    return tuple(ResultVariable(name, "unknown") for name in names)


def parse_xyce_csv(
    payload: bytes,
    *,
    analysis: str,
    title: str = "Xyce CSV result",
    limits: ResultLimits = _DEFAULT_LIMITS,
) -> SimulationResult:
    """Read ordered rectangular real columns; analysis is explicitly caller-declared.

    Re/Im columns remain separate. No step boundaries, convergence, physical
    units, or scale ordering are inferred from names or repeated sample values.
    """
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    if not isinstance(payload, bytes):
        raise ResultError("Xyce CSV input must be bytes")
    if len(payload) > limits.max_bytes:
        raise ResultError("Xyce CSV byte limit exceeded")
    _text(analysis, "caller-declared analysis")
    _text(title, "plot title")
    stream = BytesIO(payload)
    variables: tuple[ResultVariable, ...] | None = None
    columns: list[list[float | complex]] = []
    points = 0
    line_number = 0
    while raw := stream.readline(limits.max_line_bytes + 2):
        line_number += 1
        # Match raw-line counting: include CR but exclude a single LF terminator.
        content = raw.removesuffix(b"\n")
        if len(content) > limits.max_line_bytes:
            raise ResultError("Xyce CSV line byte limit exceeded")
        try:
            line = content.decode("utf-8", errors="strict").strip(" \t\r")
        except UnicodeError as error:
            raise ResultError("Xyce CSV must be UTF-8") from error
        if not line:
            continue
        if variables is None:
            variables = _header(line, limits)
            columns = [[] for _ in variables]
            continue
        points += 1
        if points > limits.max_points or points * len(variables) > limits.max_cells:
            raise ResultError("Xyce CSV point/cell limit exceeded")
        fields = line.split(",")
        if len(fields) != len(variables):
            raise ResultError(f"Xyce CSV line {line_number} has the wrong column count")
        try:
            values = [decimal_sample(value.strip()) for value in fields]
        except ResultError as error:
            raise ResultError(f"Xyce CSV line {line_number}: {error}") from error
        for column, value in zip(columns, values, strict=True):
            column.append(value)
    if variables is None or points == 0:
        raise ResultError("Xyce CSV requires one header and at least one data row")
    plot = ResultPlot(
        title,
        analysis,
        "real",
        variables,
        tuple(tuple(column) for column in columns),
        (points,),
        (("analysis_origin", "caller-declared"), ("row_grouping", "file-order-only")),
    )
    return SimulationResult("xyce-csv", hashlib.sha256(payload).hexdigest(), (plot,))


def load_xyce_csv(
    path: str | Path,
    *,
    analysis: str,
    title: str = "Xyce CSV result",
    limits: ResultLimits = _DEFAULT_LIMITS,
) -> SimulationResult:
    """Read at most the byte ceiling plus one; never execute the source or its labels."""
    if not isinstance(limits, ResultLimits):
        raise ResultError("limits must be ResultLimits")
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(limits.max_bytes + 1)
    except OSError as error:
        raise ResultError(f"cannot read Xyce CSV: {error}") from error
    return parse_xyce_csv(payload, analysis=analysis, title=title, limits=limits)
