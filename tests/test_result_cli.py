from __future__ import annotations

import json

import pytest

import spicetrellis
from spicetrellis.cli import main

RAW = b"""Title: Original parser fixture, not simulator evidence
Plotname: Operating Point
Flags: real
No. Variables: 2
No. Points: 1
Variables:
0 v(in) voltage
1 v(out) voltage
Values:
0 1
0.5
"""


def test_public_result_api_and_raw_cli_roundtrip(tmp_path, capsys) -> None:
    source = tmp_path / "original.raw"
    source.write_bytes(RAW)
    output = tmp_path / "output.json"
    assert main(["read-result", str(source), "--format", "raw", "-o", str(output)]) == 0
    result = spicetrellis.load_result(output)
    assert result == spicetrellis.load_raw(source) == spicetrellis.parse_raw(RAW)
    assert spicetrellis.load_result_text(spicetrellis.dump_result(result)) == result
    assert spicetrellis.RESULT_VERSION == 1
    assert spicetrellis.RESULT_SCHEMA == "org.spicetrellis.simulation-result"
    assert isinstance(result, spicetrellis.SimulationResult)
    assert isinstance(result.plots[0], spicetrellis.ResultPlot)
    assert isinstance(result.plots[0].variables[0], spicetrellis.ResultVariable)
    assert main(["check-result", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_sha256"] == result.source_sha256
    assert summary["plots"] == [
        {"analysis": "Operating Point", "encoding": "real", "points": 1, "variables": 2}
    ]
    assert main(["read-result", str(source), "--format", "raw"]) == 0
    assert spicetrellis.load_result_text(capsys.readouterr().out) == result


def test_csv_cli_requires_explicit_analysis_and_preserves_input_on_error(tmp_path, capsys) -> None:
    source = tmp_path / "input.csv"
    source.write_bytes(b"FREQ,Re(V(a,b)),Im(V(a,b))\n1,0.5,-0.5\n")
    before = source.read_bytes()
    base = ["read-result", str(source), "--format", "xyce-csv"]
    assert main(base) == 2
    assert "--analysis" in capsys.readouterr().err
    assert main([*base, "--analysis", "ac"]) == 0
    result = spicetrellis.load_result_text(capsys.readouterr().out)
    assert result == spicetrellis.load_xyce_csv(source, analysis="ac")
    assert result == spicetrellis.parse_xyce_csv(before, analysis="ac")
    assert main([*base, "--analysis", "ac", "--byte-order", "little"]) == 2
    assert "--byte-order" in capsys.readouterr().err
    for force in ([], ["--force"]):
        assert main([*base, "--analysis", "ac", "-o", str(source), *force]) == 2
        assert "aliases an input" in capsys.readouterr().err
        assert source.read_bytes() == before


def test_result_cli_is_atomic_and_fails_with_bounded_errors(tmp_path, capsys) -> None:
    source = tmp_path / "input.raw"
    source.write_bytes(RAW)
    output = tmp_path / "result.json"
    output.write_text("sentinel", encoding="utf-8")
    base = ["read-result", str(source), "--format", "raw"]
    assert main([*base, "--analysis", "override"]) == 2
    assert "already declares analysis" in capsys.readouterr().err
    assert main([*base, "-o", str(output)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert output.read_text() == "sentinel"
    assert main([*base, "-o", str(output), "--force"]) == 0
    assert spicetrellis.load_result(output).plots[0].columns[1] == (0.5,)
    before = output.read_bytes()
    assert main([*base, "-o", str(output), "--force", "--max-bytes", "10"]) == 2
    assert "limit" in capsys.readouterr().err
    assert output.read_bytes() == before
    assert main(["check-result", str(output), "--max-cells", "1"]) == 2
    assert "limit" in capsys.readouterr().err
    assert main([*base, "--max-bytes", "-1"]) == 2
    assert "limit" in capsys.readouterr().err
    assert not list(tmp_path.glob(".*.tmp"))


def test_result_main_module_help_is_available_in_a_clean_process(tmp_path) -> None:
    import subprocess
    import sys

    process = subprocess.run(
        [sys.executable, "-m", "spicetrellis", "read-result", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0
    assert "--format {raw,xyce-csv}" in process.stdout
    assert "--max-cells" in process.stdout


@pytest.mark.parametrize("name", ["ResultError", "ResultLimits", "write_result"])
def test_public_result_exports_are_present(name) -> None:
    assert name in spicetrellis.__all__
    assert getattr(spicetrellis, name) is not None
