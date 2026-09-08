from __future__ import annotations

import json
import runpy
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import spicetrellis as installed_package
from spicetrellis import __version__, analyze_file, fuzz_smoke, structural_summary
from spicetrellis.cli import main

ROOT = Path(__file__).parents[1]
CORPUS = ROOT / "corpus" / "portable_analog"


@pytest.mark.parametrize("name", ["two_stage_ota.sp", "rc_sensor_frontend.sp"])
def test_clean_room_corpus_has_error_free_semantics(name: str) -> None:
    analysis = analyze_file(CORPUS / name)
    assert not analysis.has_errors
    assert analysis.deck is not None


def test_structural_summary_is_content_bound_and_path_stable() -> None:
    summary = structural_summary(analyze_file(CORPUS / "two_stage_ota.sp"))
    assert summary["schema_version"] == 1
    assert summary["producer"] == {"name": "SpiceTrellis", "version": __version__}
    assert [item["path"] for item in summary["files"]] == [
        "primitives.lib",
        "two_stage_ota.sp",
    ]
    assert summary["structure"]["element_families"] == {
        "C": 1,
        "M": 5,
        "R": 4,
        "V": 3,
        "X": 3,
    }


def test_public_structural_summary_schema_accepts_producer_output() -> None:
    schema = json.loads(
        (ROOT / "docs" / "schemas" / "structural-summary-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        structural_summary(analyze_file(CORPUS / "two_stage_ota.sp"))
    )


def test_structural_summary_uses_the_analyzed_byte_snapshot(tmp_path: Path) -> None:
    deck = tmp_path / "deck.sp"
    original = b"R1 in out 1k\n.end\n"
    deck.write_bytes(original)
    analysis = analyze_file(deck)
    deck.write_bytes(b"R2 changed out 2k\n.end\n")

    record = structural_summary(analysis)["files"][0]
    assert record == {
        "path": "deck.sp",
        "bytes": len(original),
        "sha256": sha256(original).hexdigest(),
    }


def test_interop_cli_emits_the_contract(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inventory", str(CORPUS / "two_stage_ota.sp"), "--interop"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "org.spice-tools.structural-summary"


def test_fuzz_smoke_is_deterministic_and_bounded() -> None:
    seed = (CORPUS / "rc_sensor_frontend.sp").read_text(encoding="utf-8")
    assert fuzz_smoke(seed, cases=40, seed=7) == fuzz_smoke(seed, cases=40, seed=7)
    with pytest.raises(ValueError, match="cases"):
        fuzz_smoke(seed, cases=0)
    with pytest.raises(ValueError, match="cases"):
        fuzz_smoke(seed, cases=1.5)  # type: ignore[arg-type]


def test_fuzz_cli_reports_work(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(CORPUS / "rc_sensor_frontend.sp")
    assert main(["fuzz-smoke", path, "--cases", "5", "--seed", "9"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == 5


def test_benchmark_manifest_binds_every_corpus_file() -> None:
    manifest = json.loads((ROOT / "benchmarks" / "manifest.json").read_text(encoding="utf-8"))
    recorded = {ROOT / record["path"] for record in manifest["corpus"]["files"]}
    expected = set(CORPUS.glob("*.sp")) | set(CORPUS.glob("*.lib"))
    assert recorded == expected
    for record in manifest["corpus"]["files"]:
        content = (ROOT / record["path"]).read_bytes()
        assert sha256(content).hexdigest() == record["sha256"]


def test_benchmark_binds_tool_version_and_source_tree() -> None:
    module = runpy.run_path(str(ROOT / "benchmarks" / "benchmark.py"))
    result = module["run"](1)
    manifest = json.loads((ROOT / "benchmarks" / "manifest.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == 3
    assert result["tool"]["version"] == __version__
    source_digest = sha256()
    package_root = Path(installed_package.__file__).resolve().parent
    records = [
        ((Path(package_root.name) / path.relative_to(package_root)).as_posix(), path)
        for path in package_root.rglob("*.py")
    ]
    records.append(("benchmarks/benchmark.py", ROOT / "benchmarks" / "benchmark.py"))
    for logical_name, path in sorted(records):
        logical = logical_name.encode()
        content = path.read_bytes()
        source_digest.update(len(logical).to_bytes(4, "big"))
        source_digest.update(logical)
        source_digest.update(len(content).to_bytes(8, "big"))
        source_digest.update(content)
    assert result["tool"]["source_sha256"] == source_digest.hexdigest()
    assert result["tool"]["version"] == manifest["runtime"]["distribution_version"]
    assert result["tool"]["source_sha256"] == manifest["runtime"]["source_sha256"]
    digest = sha256()
    for path in sorted(set(CORPUS.glob("*.sp")) | set(CORPUS.glob("*.lib"))):
        logical = path.relative_to(ROOT).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(logical).to_bytes(4, "big"))
        digest.update(logical)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    assert result["workload_sha256"] == digest.hexdigest()
    assert result["workload_sha256"] == manifest["workload_sha256"]
    for name, value in manifest["expected"].items():
        assert result[name] == value
    scale = result["circuit_ir_scale"]
    expected_scale = manifest["circuit_ir_scale"]
    assert scale["instances"] == expected_scale["instances"]
    assert scale["serialized_bytes"] == expected_scale["serialized_bytes"]
    assert scale["workload_sha256"] == expected_scale["workload_sha256"]
    assert scale["median_ms"] <= expected_scale["maximum_median_ms"]
    for invalid in (True, 1.5, 0, 10_001):
        with pytest.raises(ValueError, match="integer between"):
            module["run"](invalid)
    for invalid in (True, 0, 20_001):
        with pytest.raises(ValueError, match="ir_instances"):
            module["run"](1, ir_instances=invalid)
    for invalid in (True, 0, 101):
        with pytest.raises(ValueError, match="ir_iterations"):
            module["run"](1, ir_iterations=invalid)
