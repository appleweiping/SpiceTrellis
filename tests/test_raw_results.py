from __future__ import annotations

import hashlib
import struct
from dataclasses import replace

import pytest

from spicetrellis.raw_results import load_raw, parse_raw
from spicetrellis.simulation_results import ResultError, ResultLimits


def raw_header(*, flags: str = "real", points: int = 2) -> bytes:
    return (
        "Title: original RC result fixture\n"
        "Date: deterministic synthetic fixture\n"
        "Plotname: Transient Analysis\n"
        f"Flags: {flags}\n"
        "No. Variables: 2\n"
        f"No. Points: {points}\n"
        "Variables:\n"
        "\t0\ttime\ttime\n"
        "\t1\tv(out)\tvoltage\n"
    ).encode()


ASCII = raw_header() + b"Values:\n0\t0\n\t1\n1\t1e-3\n\t2.5e-1\n"


def test_ascii_values_and_artifact_identity_are_preserved() -> None:
    result = parse_raw(ASCII)
    assert result.format == "spice-raw"
    assert result.source_sha256 == hashlib.sha256(ASCII).hexdigest()
    assert len(result.plots) == 1
    plot = result.plots[0]
    assert plot.analysis == "Transient Analysis"
    assert plot.variables[0].name == "time"
    assert plot.variables[1].quantity == "voltage"
    assert plot.columns == ((0.0, 0.001), (1.0, 0.25))
    assert plot.dimensions == (2,)
    assert plot.encoding == "real"


@pytest.mark.parametrize("order,prefix", [("little", "<"), ("big", ">")])
@pytest.mark.parametrize("encoding", ["real", "complex"])
def test_binary_explicit_byte_order_and_complex_stride(
    order: str, prefix: str, encoding: str
) -> None:
    numbers = (0.0, 1.0, 0.001, 0.25)
    if encoding == "complex":
        numbers = (0.0, 0.0, 1.0, -2.0, 0.001, 0.0, 0.25, 3.0)
    payload = (
        raw_header(flags=encoding)
        + b"Binary:\n"
        + struct.pack(prefix + "d" * len(numbers), *numbers)
    )
    plot = parse_raw(payload, byte_order=order).plots[0]
    assert plot.encoding == encoding
    assert plot.columns[0] == ((0j, 0.001 + 0j) if encoding == "complex" else (0.0, 0.001))
    assert plot.columns[1] == ((1 - 2j, 0.25 + 3j) if encoding == "complex" else (1.0, 0.25))


def test_ascii_complex_and_concatenated_plots() -> None:
    ac = raw_header(flags="complex") + b"Values:\n0 10,0\n 2,-3\n1 100,0\n 4,5\n"
    result = parse_raw(ASCII + b"\n" + ac)
    assert len(result.plots) == 2
    assert result.plots[1].columns == ((10 + 0j, 100 + 0j), (2 - 3j, 4 + 5j))


def test_ngspice_log_axis_display_metadata_is_preserved_without_inferring_samples() -> None:
    payload = ASCII.replace(b"\t0\ttime\ttime\n", b"\t0\tfrequency\tfrequency grid=3\n")
    plot = parse_raw(payload).plots[0]
    assert plot.variables[0].display_grid == 3
    assert plot.columns[0] == (0.0, 0.001)
    with pytest.raises(ResultError):
        parse_raw(payload.replace(b"grid=3", b"grid=999"))


def test_binary_may_be_immediately_followed_by_next_header() -> None:
    binary = raw_header(points=1) + b"Binary:\n" + struct.pack("<dd", 3.0, 4.0)
    result = parse_raw(binary + ASCII, byte_order="little")
    assert len(result.plots) == 2
    assert result.plots[0].columns == ((3.0,), (4.0,))


def test_binary_without_byte_order_is_never_guessed() -> None:
    with pytest.raises(ResultError, match="byte_order"):
        parse_raw(raw_header(points=1) + b"Binary:\n" + struct.pack("<dd", 1.0, 2.0))


@pytest.mark.parametrize(
    "payload",
    [
        ASCII.replace(b"1\t1e-3", b"2\t1e-3"),
        ASCII.replace(b"\t1\tv(out)", b"\t0\tv(out)"),
        ASCII.replace(b"\t1\tv(out)", b"\t1\tTIME"),
        ASCII.replace(b"Flags: real", b"Flags: real complex"),
        ASCII.replace(b"Flags: real", b"Flags: real fastaccess"),
        ASCII.replace(b"Flags: real", b"Flags: real unpadded"),
        ASCII.replace(b"No. Points: 2", b"No. Points: -2"),
        ASCII.replace(b"No. Points: 2", b"No. Points: true"),
        ASCII.replace(b"No. Points: 2", b"No. Points: 3"),
        ASCII.replace(b"1e-3", b"nan"),
        ASCII.replace(b"1e-3", b"1e999"),
        ASCII.replace(b"1e-3", b"1e-999"),
        ASCII.replace(b"1e-3", b"0x1p0"),
        ASCII.replace(b"\t1\tv(out)\tvoltage", b"\t1\tv(out)\tvoltage dims=1"),
        ASCII.replace(b"Flags: real", b"Flags: real\nFlags: real"),
        ASCII + b"unexplained trailing data",
        ASCII[:-3],
        b"",
    ],
)
def test_malformed_or_unsupported_results_fail_closed(payload: bytes) -> None:
    with pytest.raises(ResultError):
        parse_raw(payload)


def test_explicit_shape_is_checked_and_preserved() -> None:
    shaped = ASCII.replace(b"Variables:\n", b"Dimensions: 1,2\nVariables:\n")
    assert parse_raw(shaped).plots[0].dimensions == (1, 2)
    with pytest.raises(ResultError, match="Dimensions"):
        parse_raw(shaped.replace(b"Dimensions: 1,2", b"Dimensions: 3,2"))


def test_binary_truncation_nonfinite_and_budget_checks() -> None:
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ResultError, match="finite"):
            parse_raw(
                raw_header(points=1) + b"Binary:\n" + struct.pack("<dd", 0.0, value),
                byte_order="little",
            )
    with pytest.raises(ResultError, match="truncated"):
        parse_raw(raw_header() + b"Binary:\n" + b"\0" * 15, byte_order="little")
    for limits in (
        ResultLimits(max_bytes=10),
        ResultLimits(max_variables=1),
        ResultLimits(max_points=1),
        ResultLimits(max_cells=3),
    ):
        with pytest.raises(ResultError, match="limit"):
            parse_raw(ASCII, limits=limits)
    with pytest.raises(ResultError, match="limit"):
        parse_raw(ASCII * 2, limits=ResultLimits(max_plots=1))


def test_file_reads_are_bounded_and_input_is_not_modified(tmp_path) -> None:
    path = tmp_path / "result.raw"
    path.write_bytes(ASCII)
    assert load_raw(path) == parse_raw(ASCII)
    with pytest.raises(ResultError, match="limit"):
        load_raw(path, limits=ResultLimits(max_bytes=10))
    assert path.read_bytes() == ASCII
    with pytest.raises(ResultError, match="read"):
        load_raw(tmp_path / "absent.raw")


def test_public_types_reject_invalid_replacements() -> None:
    plot = parse_raw(ASCII).plots[0]
    with pytest.raises(ResultError):
        replace(plot, columns=((0.0,), (float("nan"),)))
    with pytest.raises(ResultError):
        replace(plot, encoding="complex")
    with pytest.raises(ResultError):
        ResultLimits(max_points=True)


def test_metadata_cannot_contradict_canonical_plot_fields() -> None:
    plot = parse_raw(ASCII).plots[0]
    assert plot.metadata == (("date", "deterministic synthetic fixture"),)
    for key in ("title", "Plotname", "flags", "no. variables", "no. points", "dimensions"):
        with pytest.raises(ResultError, match="canonical"):
            replace(plot, metadata=((key, "999"),))


def test_variable_budget_is_cumulative_across_appended_plots() -> None:
    with pytest.raises(ResultError, match="limit"):
        parse_raw(ASCII + ASCII, limits=ResultLimits(max_variables=2, max_points=4, max_cells=8))
