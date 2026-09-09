from __future__ import annotations

import math
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_xyce_ramp_oracle_has_expected_causal_and_steady_response() -> None:
    response = runpy.run_path(str(ROOT / "tools/check_xyce_results.py"))["_ramp_response"]
    assert response(0) == response(0.001) == 0
    assert 0 < response(0.0010005) < response(0.001001) < 0.001
    assert response(0.001001 + 0.001) == pytest.approx(1 - math.exp(-1), abs=0.0005)
    assert response(0.101) == 1


def test_xyce_dc_oracle_rejects_wrong_shape_and_detects_wrong_values() -> None:
    check = runpy.run_path(str(ROOT / "tools/check_xyce_results.py"))["_check"]
    correct = {
        "V(A)": tuple(i / 10 for i in range(11)),
        "V(B)": tuple(i / 20 for i in range(11)),
        "V(A,B)": tuple(i / 20 for i in range(11)),
    }
    assert check("dc", correct) == (0, 1e-12)
    wrong = dict(correct, **{"V(B)": (0.0,) * 11})
    error, tolerance = check("dc", wrong)
    assert error > tolerance
    with pytest.raises(AssertionError, match="11 requested"):
        check("dc", {"V(B)": (0.0,)})


def test_xyce_ac_and_transient_oracles_reject_truncated_experiments() -> None:
    check = runpy.run_path(str(ROOT / "tools/check_xyce_results.py"))["_check"]
    with pytest.raises(AssertionError, match="33 requested"):
        check("ac", {"FREQ": (1.0,)})
    with pytest.raises(AssertionError, match="span"):
        check("tran", {"TIME": (0.0, 0.001)})


def test_ngspice_transient_oracle_rejects_a_truncated_experiment() -> None:
    check = runpy.run_path(str(ROOT / "tools/check_ngspice_results.py"))["_check_transient"]
    with pytest.raises(AssertionError, match="span"):
        check((0.0,), (0.0,))


@pytest.mark.parametrize("script", ["check_ngspice_results.py", "check_xyce_results.py"])
def test_real_simulator_harness_does_not_substitute_fake_results(script, monkeypatch) -> None:
    harness = runpy.run_path(str(ROOT / "tools" / script))
    monkeypatch.setattr(harness["shutil"], "which", lambda _name: None)
    with pytest.raises(RuntimeError, match=r"real .* executable"):
        harness["run"]("missing-simulator")
