"""Exercise real Xyce CSV ingestion against independent DC, AC and transient RC oracles."""

from __future__ import annotations

import argparse
import cmath
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from spicetrellis import dump_result, load_result_text, load_xyce_csv

DECKS = {
    "dc": """* Original two-resistor DC divider result oracle
V1 a 0 DC 1
R1 a b 1k
R2 b 0 1k
.DC V1 0 1 0.1
.PRINT DC FORMAT = CSV FILE = result.csv PRECISION = 16 V(a) V(b) V(a,b)
.END
""",
    "ac": """* Original single-pole RC complex projection result oracle
V1 a 0 AC 1
R1 a b 1k
C1 b 0 1u
.AC DEC 8 1 10k
.PRINT AC FORMAT = CSV FILE = result.csv PRECISION = 16
+ V(a) V(b) V(a,b) VR(a,b) VI(a,b) VM(a,b) VP(a,b)
.END
""",
    "tran": """* Original finite-rise RC step-response result oracle
V1 a 0 PULSE(0 1 1m 1u 1u 20m 40m)
R1 a b 1k
C1 b 0 1u
.TRAN 10u 3m
.OPTIONS TIMEINT RELTOL = 1e-8 ABSTOL = 1e-12
.PRINT TRAN FORMAT = CSV FILE = result.csv PRECISION = 16 V(a) V(b) V(a,b)
.END
""",
}


def _ramp_response(time: float) -> float:
    elapsed, rise, tau = time - 0.001, 1e-6, 0.001
    if elapsed <= 0:
        return 0.0
    if elapsed < rise:
        return (elapsed + tau * math.expm1(-elapsed / tau)) / rise
    return 1 + tau / rise * math.expm1(-rise / tau) * math.exp(-(elapsed - rise) / tau)


def _check(analysis: str, vectors: dict[str, tuple[float | complex, ...]]) -> tuple[float, float]:
    error = 0.0
    if analysis == "dc":
        if len(vectors["V(B)"]) != 11:
            raise AssertionError("DC sweep must produce exactly 11 requested points")
        for index, (input_value, output, difference) in enumerate(
            zip(vectors["V(A)"], vectors["V(B)"], vectors["V(A,B)"], strict=True)
        ):
            error = max(
                error,
                abs(input_value - index / 10),
                abs(output - index / 20),
                abs(difference - index / 20),
            )
        return error, 1e-12
    if analysis == "ac":
        if len(vectors["FREQ"]) != 33:
            raise AssertionError("AC sweep must produce exactly 33 requested frequencies")
        for index, frequency in enumerate(vectors["FREQ"]):
            transfer = 1 / (1 + 2j * math.pi * frequency.real * 0.001)
            drop = 1 - transfer
            expected = {
                "Re(V(A))": 1.0,
                "Im(V(A))": 0.0,
                "Re(V(B))": transfer.real,
                "Im(V(B))": transfer.imag,
                "Re(V(A,B))": drop.real,
                "Im(V(A,B))": drop.imag,
                "VR(A,B)": drop.real,
                "VI(A,B)": drop.imag,
                "VM(A,B)": abs(drop),
                "VP(A,B)": math.degrees(cmath.phase(drop)),
            }
            for name, value in expected.items():
                error = max(error, abs(vectors[name][index] - value))
        return error, 1e-10
    times = vectors["TIME"]
    if len(times) < 20 or times[0] != 0 or abs(times[-1] - 0.003) > 1e-12:
        raise AssertionError("transient artifact does not span the requested experiment")
    for time, input_value, output, difference in zip(
        times, vectors["V(A)"], vectors["V(B)"], vectors["V(A,B)"], strict=True
    ):
        error = max(
            error, abs(output - _ramp_response(time.real)), abs(difference - (input_value - output))
        )
    return error, 1e-5


def run(executable: str, *, timeout: float = 120.0) -> dict[str, object]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise RuntimeError("a real Xyce executable is required")
    if not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError("timeout must be finite and in 1..600 seconds")
    executable_path = str(Path(resolved).resolve())
    version = subprocess.run(
        [executable_path, "-v"], capture_output=True, text=True, check=True, timeout=timeout
    )
    records = []
    with tempfile.TemporaryDirectory(prefix="spicetrellis-xyce-results-") as directory:
        root = Path(directory)
        for analysis, deck in DECKS.items():
            case = root / analysis
            case.mkdir()
            netlist = case / "original.cir"
            netlist.write_text(deck, encoding="ascii", newline="\n")
            subprocess.run(
                [executable_path, "-l", "xyce.log", netlist.name],
                cwd=case,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            result = load_xyce_csv(case / "result.csv", analysis=analysis)
            if load_result_text(dump_result(result)) != result or len(result.plots) != 1:
                raise AssertionError("CSV/JSON round trip changed the parsed result")
            plot = result.plots[0]
            vectors = {
                variable.name: column
                for variable, column in zip(plot.variables, plot.columns, strict=True)
            }
            error, tolerance = _check(analysis, vectors)
            if error > tolerance:
                raise AssertionError(f"{analysis} analytical error {error} exceeds {tolerance}")
            records.append(
                {
                    "analysis": analysis,
                    "points": plot.points,
                    "variables": [variable.name for variable in plot.variables],
                    "source_sha256": result.source_sha256,
                    "netlist_sha256": hashlib.sha256(netlist.read_bytes()).hexdigest(),
                    "solver_log_sha256": hashlib.sha256(
                        (case / "xyce.log").read_bytes()
                    ).hexdigest(),
                    "maximum_absolute_error": error,
                    "absolute_tolerance": tolerance,
                }
            )
    return {
        "schema_version": 1,
        "experiment": "original-xyce-csv-analytical-oracles",
        "simulator_version_output": (version.stdout + version.stderr).strip(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "oracles": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xyce", default="Xyce")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    print(json.dumps(run(args.xyce, timeout=args.timeout), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
