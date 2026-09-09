"""Reproduce independent RC analytical oracles using real ngspice output files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from spicetrellis.raw_results import load_raw

DECK = """* Original RC analytical and raw-format integration fixture
Vinput input 0 DC 1 AC 1 PULSE(0 1 1m 1n 1n 10m 20m)
Rfilter input output 1k
Cfilter output 0 1u
.control
set filetype=ascii
op
write op-ascii.raw all
dc Vinput 0 1 0.1
write dc-ascii.raw all
ac dec 8 1 10000
write ac-ascii.raw all
set filetype=binary
write ac-binary.raw all
tran 10u 5m
write tran-binary.raw all
set filetype=ascii
write tran-ascii.raw all
quit
.endc
.end
"""


def _check_transient(
    times: tuple[float | complex, ...], outputs: tuple[float | complex, ...]
) -> tuple[float, float]:
    if len(times) < 20 or times[0].real != 0 or abs(times[-1].real - 0.005) > 1e-12:
        raise AssertionError("transient artifact does not span the requested experiment")
    error = 0.0
    for time, output in zip(times, outputs, strict=True):
        elapsed = time.real - 0.001
        expected = 0.0 if elapsed <= 0 else -math.expm1(-elapsed / 0.001)
        error = max(error, abs(output - expected))
    return error, 1e-5


def run(executable: str, *, timeout: float = 60.0) -> dict[str, object]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise RuntimeError("a real ngspice executable is required")
    if not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError("timeout must be finite and in 1..600 seconds")
    executable_path = str(Path(resolved).resolve())
    version = subprocess.run(
        [executable_path, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="spicetrellis-ngspice-results-") as directory:
        root = Path(directory)
        netlist = root / "rc.cir"
        netlist.write_text(DECK, encoding="ascii", newline="\n")
        # -n excludes user/system .spiceinit commands from this fixed experiment.
        subprocess.run(
            [executable_path, "-n", "-b", "-o", "ngspice.log", netlist.name],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        records: list[dict[str, object]] = []
        for name in (
            "op-ascii",
            "dc-ascii",
            "ac-ascii",
            "ac-binary",
            "tran-ascii",
            "tran-binary",
        ):
            artifact = load_raw(root / f"{name}.raw", byte_order=sys.byteorder)
            if len(artifact.plots) != 1:
                raise AssertionError("the fixed experiment must produce one plot per artifact")
            plot = artifact.plots[0]
            vectors = {
                variable.name: column
                for variable, column in zip(plot.variables, plot.columns, strict=True)
            }
            error = 0.0
            tolerance = 0.0
            if name == "op-ascii":
                if not (
                    vectors["v(input)"] == vectors["v(output)"] == (1.0,)
                    and vectors["i(vinput)"] == (0.0,)
                ):
                    raise AssertionError("operating point violates passive DC oracle")
            elif name == "dc-ascii":
                if plot.points != 11:
                    raise AssertionError("DC sweep cardinality differs from its explicit step")
                tolerance = 1e-12
                for index, output in enumerate(vectors["v(output)"]):
                    error = max(error, abs(output - index / 10))
            elif name.startswith("ac-"):
                if plot.points != 33 or plot.encoding != "complex":
                    raise AssertionError("AC result shape or complex encoding is incorrect")
                tolerance = 1e-12
                for frequency, output in zip(
                    vectors["frequency"], vectors["v(output)"], strict=True
                ):
                    expected = 1 / (1 + 2j * math.pi * frequency.real * 0.001)
                    error = max(error, abs(output - expected))
            else:
                error, tolerance = _check_transient(vectors["time"], vectors["v(output)"])
            if error > tolerance:
                raise AssertionError(f"{name} analytical error {error} exceeds {tolerance}")
            records.append(
                {
                    "artifact": name + ".raw",
                    "source_sha256": artifact.source_sha256,
                    "points": plot.points,
                    "variables": len(plot.variables),
                    "encoding": plot.encoding,
                    "maximum_absolute_error": error,
                    "absolute_tolerance": tolerance,
                }
            )
        return {
            "schema_version": 1,
            "experiment": "original-passive-rc-raw-results",
            "netlist_sha256": hashlib.sha256(netlist.read_bytes()).hexdigest(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "simulator_version_output": version.strip(),
            "binary_byte_order": sys.byteorder,
            "oracles": records,
            "solver_log_sha256": hashlib.sha256((root / "ngspice.log").read_bytes()).hexdigest(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    print(json.dumps(run(args.ngspice, timeout=args.timeout), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
