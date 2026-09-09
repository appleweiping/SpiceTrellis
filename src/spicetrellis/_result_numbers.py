"""Strict decimal-to-binary64 conversion shared by result text formats."""

from __future__ import annotations

import math
import re

from spicetrellis.simulation_results import ResultError

_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def decimal_sample(value: str) -> float:
    if len(value) > 128 or not _DECIMAL.fullmatch(value):
        raise ResultError("result value must be a bounded decimal number")
    number = float(value)
    if not math.isfinite(number):
        raise ResultError("result values must be finite")
    mantissa = re.split("[eE]", value, maxsplit=1)[0]
    if number == 0 and any(char in "123456789" for char in mantissa):
        raise ResultError("result decimal value underflows the binary64 representation")
    return number
