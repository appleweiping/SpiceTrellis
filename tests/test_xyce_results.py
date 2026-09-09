from __future__ import annotations

import hashlib
import tracemalloc

import pytest

from spicetrellis.result_json import dump_result, load_result_text
from spicetrellis.simulation_results import ResultError, ResultLimits
from spicetrellis.xyce_results import load_xyce_csv, parse_xyce_csv


def test_csv_preserves_unquoted_differential_voltage_and_complex_projection_names() -> None:
    payload = b"FREQ,Re(V(a,b)),Im(V(a,b)),VM(a,b),VP(a,b)\n1,0.5,-0.5,0.707,-45\n"
    result = parse_xyce_csv(payload, analysis="ac")
    plot = result.plots[0]
    assert result.format == "xyce-csv"
    assert result.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert [item.name for item in plot.variables] == [
        "FREQ",
        "Re(V(a,b))",
        "Im(V(a,b))",
        "VM(a,b)",
        "VP(a,b)",
    ]
    assert {item.quantity for item in plot.variables} == {"unknown"}
    assert plot.columns == ((1.0,), (0.5,), (-0.5,), (0.707,), (-45.0,))
    assert plot.encoding == "real"
    assert plot.analysis == "ac"
    assert ("analysis_origin", "caller-declared") in plot.metadata
    assert load_result_text(dump_result(result)) == result


def test_csv_preserves_blank_lines_crlf_and_step_rows_without_guessing_grouping() -> None:
    payload = b"\r\n Index , TIME , V(out) \r\n0,0,0\r\n1,1e-3,1\r\n\r\n0,0,2\r\n"
    result = parse_xyce_csv(payload, analysis="tran", title="two declared sweeps")
    assert len(result.plots) == 1
    plot = result.plots[0]
    assert plot.columns == ((0.0, 1.0, 0.0), (0.0, 0.001, 0.0), (0.0, 1.0, 2.0))
    assert plot.dimensions == (3,)
    assert plot.title == "two declared sweeps"
    assert ("row_grouping", "file-order-only") in plot.metadata


def test_csv_balanced_expression_names_are_preserved_but_never_executed() -> None:
    payload = b"TIME,{MAX(V(a,b),V(c))}\n0,1\n"
    result = parse_xyce_csv(payload, analysis="custom")
    assert result.plots[0].variables[1].name == "{MAX(V(a,b),V(c))}"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"TIME,V(out)\n",
        b"TIME,V(out)\n0\n",
        b"TIME\n0,1\n",
        b"TIME,TIME\n0,1\n",
        b"Time,TIME\n0,1\n",
        b"TIME,\n0,1\n",
        b"TIME,V(a,b\n0,1\n",
        b"TIME,V(a,b)}\n0,1\n",
        b"TIME,{V(a,b)}\n0,1,2\n",
        b'TIME,"V(out)"\n0,1\n',
        b"TIME,V(a[0])\n0,1\n",
        b"TIME,V(a\\b)\n0,1\n",
        b"TIME,V(out)\n0,NaN\n",
        b"TIME,V(out)\n0,inf\n",
        b"TIME,V(out)\n0,1e999\n",
        b"TIME,V(out)\n0,1e-999\n",
        b"TIME,V(out)\n0,1k\n",
        b"TIME,V(out)\n0,1_000\n",
        b"TIME,V(out)\n0,1\nEnd of Xyce Simulation\n",
        b"TIME,V(out)\n0,1\nTIME,V(out)\n0,2\n",
        b"\xff\n0\n",
        "TIME,V(out\u202ename)\n0,1\n".encode(),
    ],
)
def test_malformed_or_unsupported_profiles_fail_closed(payload) -> None:
    with pytest.raises(ResultError):
        parse_xyce_csv(payload, analysis="tran")


def test_complete_limits_and_header_depth() -> None:
    payload = b"TIME,V(out)\n0,1\n1,2\n"
    for limits in (
        ResultLimits(max_bytes=10),
        ResultLimits(max_variables=1),
        ResultLimits(max_points=1),
        ResultLimits(max_cells=3),
        ResultLimits(max_line_bytes=3),
    ):
        with pytest.raises(ResultError, match="limit"):
            parse_xyce_csv(payload, analysis="tran", limits=limits)
    with pytest.raises(ResultError, match="nesting limit"):
        parse_xyce_csv(b"(" * 33 + b"V(a)" + b")" * 33 + b"\n0\n", analysis="tran")
    assert (
        parse_xyce_csv(
            payload, analysis="tran", limits=ResultLimits(max_bytes=len(payload), max_line_bytes=11)
        )
        .plots[0]
        .points
        == 2
    )


def test_file_reader_and_invalid_caller_inputs(tmp_path) -> None:
    path = tmp_path / "result.csv"
    payload = b"V(out)\n1\n"
    path.write_bytes(payload)
    assert load_xyce_csv(path, analysis="op") == parse_xyce_csv(payload, analysis="op")
    with pytest.raises(ResultError, match="read"):
        load_xyce_csv(tmp_path / "missing", analysis="op")
    with pytest.raises(ResultError, match="limit"):
        load_xyce_csv(path, analysis="op", limits=ResultLimits(max_bytes=1))
    for kwargs in (
        {"analysis": ""},
        {"analysis": "op", "title": ""},
        {"analysis": "op", "limits": 1},
    ):
        with pytest.raises(ResultError):
            parse_xyce_csv(payload, **kwargs)
    with pytest.raises(ResultError, match="bytes"):
        parse_xyce_csv("V(out)\n1", analysis="op")
    with pytest.raises(ResultError, match="limits"):
        load_xyce_csv(path, analysis="op", limits=1)


def test_oversized_physical_line_is_rejected_without_copying_the_whole_line() -> None:
    payload = b"A" * 9_999_999 + b"\nB"
    tracemalloc.start()
    try:
        with pytest.raises(ResultError, match="line byte limit"):
            parse_xyce_csv(payload, analysis="op")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
