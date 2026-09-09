from __future__ import annotations

import json
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from spicetrellis.result_json import dump_result, load_result, load_result_text, write_result
from spicetrellis.simulation_results import (
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
)


def example(encoding: str = "real") -> SimulationResult:
    columns = ((0.0, 1.0), (1.0, 0.5))
    if encoding == "complex":
        columns = ((0j, 1 + 0j), (1 - 2j, 0.5 + 3j))
    return SimulationResult(
        "spice-raw",
        "a" * 64,
        (
            ResultPlot(
                "original",
                "AC" if encoding == "complex" else "transient",
                encoding,
                (ResultVariable("time", "time", 3), ResultVariable("v(out)", "voltage")),
                columns,
                (2,),
                (("date", "original fixture"),),
            ),
        ),
    )


@pytest.mark.parametrize("encoding", ["real", "complex"])
def test_result_json_roundtrip_preserves_values_metadata_and_identity(encoding: str) -> None:
    result = example(encoding)
    text = dump_result(result)
    assert load_result_text(text) == result
    assert load_result_text(text.encode()) == result
    assert dump_result(load_result_text(text)) == text
    body = json.loads(text)
    assert body["schema"] == "org.spicetrellis.simulation-result"
    assert body["schema_version"] == 1
    assert body["source_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(schema_version=True),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(schema="different"),
        lambda d: d.update(extra="hidden"),
        lambda d: d.pop("source_sha256"),
        lambda d: d.update(source_sha256="uppercase" * 8),
        lambda d: d.update(plots=[]),
        lambda d: d.update(plots={}),
        lambda d: d["plots"][0].update(extra="hidden"),
        lambda d: d["plots"][0].update(encoding="complex"),
        lambda d: d["plots"][0].update(encoding=[]),
        lambda d: d["plots"][0].update(columns=[[0, 1], [2]]),
        lambda d: d["plots"][0].update(columns=[[0, 1], [True, False]]),
        lambda d: d["plots"][0].update(columns=[[0, 1], ["1", "2"]]),
        lambda d: d["plots"][0].update(dimensions=[True]),
        lambda d: d["plots"][0].update(dimensions=[3]),
        lambda d: d["plots"][0]["variables"][0].update(display_grid=True),
        lambda d: d["plots"][0]["variables"][0].update(extra=0),
        lambda d: d["plots"][0]["variables"][1].update(name="TIME"),
        lambda d: d["plots"][0].update(metadata=[["flags", "complex"]]),
        lambda d: d["plots"][0].update(metadata=[[" flags ", "complex"]]),
        lambda d: d["plots"][0].update(metadata=[["\u00a0flags", "complex"]]),
        lambda d: d["plots"][0].update(metadata=[["a", "b"], ["A", "c"]]),
        lambda d: d["plots"][0].update(metadata=[["a"]]),
        lambda d: d["plots"][0].update(title="bad\u202ename"),
    ],
)
def test_invalid_wire_fields_fail_with_typed_errors(mutation) -> None:
    body = json.loads(dump_result(example()))
    mutation(body)
    with pytest.raises(ResultError):
        load_result_text(json.dumps(body))


@pytest.mark.parametrize(
    "payload",
    [
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e999}',
        '{"x":1e-999}',
        '{"x":' + "9" * 129 + "}",
        "[" * 1000 + "0" + "]" * 1000,
        "null",
        "[]",
        "",
        b"\xff",
    ],
)
def test_json_parser_limits_duplicate_keys_and_nonfinite_numbers(payload) -> None:
    with pytest.raises(ResultError):
        load_result_text(payload)


def test_all_artifact_limits_apply_to_json() -> None:
    text = dump_result(example())
    for limits in (
        ResultLimits(max_bytes=10),
        ResultLimits(max_variables=1),
        ResultLimits(max_points=1),
        ResultLimits(max_cells=3),
    ):
        with pytest.raises(ResultError, match="limit"):
            load_result_text(text, limits=limits)
    result = example()
    repeated = SimulationResult(result.format, result.source_sha256, result.plots * 2)
    with pytest.raises(ResultError, match="limit"):
        load_result_text(dump_result(repeated), limits=ResultLimits(max_plots=1))
    with pytest.raises(ResultError, match="limit"):
        load_result_text(dump_result(repeated), limits=ResultLimits(max_variables=2))


def test_output_is_atomic_no_clobber_and_protects_source_paths(tmp_path) -> None:
    path = tmp_path / "result.json"
    result = example()
    write_result(result, path)
    assert load_result(path) == result
    before = path.read_bytes()
    with pytest.raises((ResultError, ValueError)):
        write_result(result, path)
    assert path.read_bytes() == before
    write_result(result, path, force=True)
    with pytest.raises((ResultError, ValueError)):
        write_result(result, path, force=True, protected=(path,))
    assert path.read_bytes() == before
    with pytest.raises(ResultError, match="limit"):
        load_result(path, limits=ResultLimits(max_bytes=10))
    with pytest.raises(ResultError, match="read"):
        load_result(tmp_path / "absent")


@pytest.mark.parametrize(
    "limits",
    [
        ResultLimits(max_bytes=10),
        ResultLimits(max_variables=1),
        ResultLimits(max_points=1),
        ResultLimits(max_cells=3),
    ],
)
def test_writer_enforces_the_same_limits_as_reader(limits, tmp_path) -> None:
    with pytest.raises(ResultError, match="limit"):
        dump_result(example(), limits=limits)
    path = tmp_path / "result.json"
    with pytest.raises(ResultError, match="limit"):
        write_result(example(), path, limits=limits)
    assert not path.exists()


def test_writer_enforces_complete_counts_and_exact_byte_boundary() -> None:
    result = example("complex")
    repeated = SimulationResult(result.format, result.source_sha256, result.plots * 2)
    for limits in (
        ResultLimits(max_plots=1),
        ResultLimits(max_variables=2),
        ResultLimits(max_points=2),
        ResultLimits(max_cells=4),
    ):
        with pytest.raises(ResultError, match="limit"):
            dump_result(repeated, limits=limits)
    payload = dump_result(result)
    limits = ResultLimits(max_bytes=len(payload.encode("utf-8")))
    assert load_result_text(dump_result(result, limits=limits), limits=limits) == result
    with pytest.raises(ResultError, match="limit"):
        dump_result(result, limits=ResultLimits(max_bytes=limits.max_bytes - 1))


def test_result_wire_rejects_non_utf8_encodings() -> None:
    payload = dump_result(example())
    for encoding in ("utf-16", "utf-32"):
        with pytest.raises(ResultError):
            load_result_text(payload.encode(encoding))


def test_full_metadata_remains_readable_with_exact_small_count_limits() -> None:
    result = example()
    plot = replace(result.plots[0], metadata=tuple((f"key{i}", "data") for i in range(128)))
    result = replace(result, plots=(plot,))
    limits = ResultLimits(max_plots=1, max_variables=2, max_points=2, max_cells=4)
    assert load_result_text(dump_result(result, limits=limits), limits=limits) == result


@pytest.mark.parametrize("encoding", ["real", "complex"])
def test_published_schema_matches_real_and_complex_wire_shape(encoding) -> None:
    schema = json.loads(
        Path("docs/schemas/simulation-result-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    body = json.loads(dump_result(example(encoding)))
    validator.validate(body)
    body["plots"][0]["columns"][1][0] = True
    assert list(validator.iter_errors(body))


def test_byte_limit_stops_writer_without_materializing_a_duplicate_sample_graph() -> None:
    result = example("complex")
    plot = replace(result.plots[0], columns=((1 + 2j,) * 100_000,) * 2, dimensions=(100_000,))
    result = replace(result, plots=(plot,))
    tracemalloc.start()
    try:
        with pytest.raises(ResultError, match="byte limit"):
            dump_result(result, limits=ResultLimits(max_bytes=500))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_complexity_walk_uses_depth_not_width_auxiliary_memory() -> None:
    from spicetrellis.result_json import _complexity

    wide = [0.0] * 100_000
    tracemalloc.start()
    try:
        _complexity(wide, ResultLimits())
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_explicit_json_depth_and_node_limits_are_interpreter_independent() -> None:
    from spicetrellis.result_json import _complexity

    limits = ResultLimits(max_cells=1, max_points=1, max_variables=1, max_plots=1)
    with pytest.raises(ResultError, match="node limit"):
        _complexity([0] * 600, limits)
    with pytest.raises(ResultError, match="depth limit"):
        load_result_text("[" * 35 + "0" + "]" * 35)
