"""Reproducible corpus benchmark with deterministic work counters."""

from __future__ import annotations

import argparse
import json
import statistics
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import spicetrellis as installed_package
from spicetrellis import __version__, analyze_file, build_ir, dump_ir, inventory

ROOT = Path(__file__).parents[1]
CORPUS_ROOT = ROOT / "corpus" / "portable_analog"
DECKS = tuple(sorted(CORPUS_ROOT.glob("*.sp")))
CORPUS_FILES = tuple(sorted((*CORPUS_ROOT.glob("*.sp"), *CORPUS_ROOT.glob("*.lib"))))


def _source_sha256() -> str:
    """Bind a result to the imported implementation and this benchmark harness."""

    digest = sha256()
    package_root = Path(installed_package.__file__).resolve().parent
    records = [
        ((Path(package_root.name) / path.relative_to(package_root)).as_posix(), path)
        for path in package_root.rglob("*.py")
    ]
    records.append(("benchmarks/benchmark.py", Path(__file__).resolve()))
    for logical_name, path in sorted(records):
        logical = logical_name.encode()
        content = path.read_bytes()
        digest.update(len(logical).to_bytes(4, "big"))
        digest.update(logical)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _scale_deck(instances: int) -> str:
    if type(instances) is not int or not 1 <= instances <= 20_000:
        raise ValueError("ir_instances must be an integer between 1 and 20000")
    elements = "".join(f"R{index} n{index} 0 1k\n" for index in range(instances))
    return "* deterministic clean-room Circuit IR scale workload\n" + elements + ".end\n"


def run(iterations: int, *, ir_instances: int = 5_000, ir_iterations: int = 1) -> dict[str, object]:
    if type(iterations) is not int or not 1 <= iterations <= 10_000:
        raise ValueError("iterations must be an integer between 1 and 10000")
    if type(ir_iterations) is not int or not 1 <= ir_iterations <= 100:
        raise ValueError("ir_iterations must be an integer between 1 and 100")
    samples: list[float] = []
    statements = 0
    digest = sha256()
    for path in CORPUS_FILES:
        logical = path.relative_to(ROOT).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(logical).to_bytes(4, "big"))
        digest.update(logical)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    for _ in range(iterations):
        started = perf_counter()
        current = 0
        for path in DECKS:
            result = analyze_file(path)
            if result.has_errors:
                raise RuntimeError(f"benchmark corpus failed analysis: {path.name}")
            current += sum(count for _family, count in inventory(result).element_families)
        samples.append((perf_counter() - started) * 1000)
        statements = current

    scale_text = _scale_deck(ir_instances)
    ir_samples: list[float] = []
    ir_bytes = 0
    with TemporaryDirectory(prefix="spicetrellis-benchmark-") as directory:
        scale_path = Path(directory) / "ir-scale.sp"
        scale_path.write_text(scale_text, encoding="utf-8")
        analysis = analyze_file(scale_path)
        if analysis.has_errors:
            raise RuntimeError("generated Circuit IR scale workload failed analysis")
        for _ in range(ir_iterations):
            started = perf_counter()
            circuit = build_ir(analysis)
            rendered = dump_ir(circuit, pretty=False)
            ir_samples.append((perf_counter() - started) * 1000)
            if circuit.instance_count != ir_instances:
                raise RuntimeError("Circuit IR scale workload returned the wrong instance count")
            ir_bytes = len(rendered.encode("utf-8"))
    return {
        "schema_version": 3,
        "tool": {
            "name": "SpiceTrellis",
            "version": __version__,
            "source_sha256": _source_sha256(),
        },
        "workload_sha256": digest.hexdigest(),
        "iterations": iterations,
        "decks_per_iteration": len(DECKS),
        "elements_per_iteration": statements,
        "median_ms": round(statistics.median(samples), 6),
        "minimum_ms": round(min(samples), 6),
        "circuit_ir_scale": {
            "workload_sha256": sha256(scale_text.encode("utf-8")).hexdigest(),
            "instances": ir_instances,
            "iterations": ir_iterations,
            "serialized_bytes": ir_bytes,
            "median_ms": round(statistics.median(ir_samples), 6),
            "minimum_ms": round(min(ir_samples), 6),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--ir-instances", type=int, default=5_000)
    parser.add_argument("--ir-iterations", type=int, default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.iterations,
                ir_instances=args.ir_instances,
                ir_iterations=args.ir_iterations,
            ),
            indent=2,
            sort_keys=True,
        )
    )
