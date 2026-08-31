# Benchmarks

Run `python benchmarks/benchmark.py --iterations 25` from an installed development environment.
The JSON records a content-derived workload hash, deterministic work counts, the installed package
version, and a SHA-256 identity covering both the actual imported Python package tree and benchmark
harness under test. Timing is diagnostic:
compare it only on the same machine, interpreter, power mode, and source identity. It is not a CI
threshold.

The corpus and its license/provenance are recorded in `manifest.json`.
