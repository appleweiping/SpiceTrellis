"""Reproducible corpus benchmark with deterministic work counters."""

from __future__ import annotations

import argparse
import json
import statistics
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import spicetrellis as installed_package
from spicetrellis import __version__, analyze_file, inventory

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


def run(iterations: int) -> dict[str, object]:
    if type(iterations) is not int or not 1 <= iterations <= 10_000:
        raise ValueError("iterations must be an integer between 1 and 10000")
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
    return {
        "schema_version": 2,
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
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(run(args.iterations), indent=2, sort_keys=True))
