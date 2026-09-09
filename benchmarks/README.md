# Benchmarks

Run `python benchmarks/benchmark.py --iterations 25` from an installed development environment.
The JSON records a content-derived workload hash, deterministic work counts, the installed package
version, and a SHA-256 identity covering both the actual imported Python package tree and benchmark
harness under test. Timing is diagnostic:
compare it only on the same machine, interpreter, power mode, and source identity. It is not a CI
threshold.

The corpus and its license/provenance are recorded in `manifest.json`.

`physical-v060-regression-20260909.json` is the actual unchanged parser/Circuit IR
workload replay after adding raw physical libraries, run with 25 parser iterations
and one 5,000-instance IR iteration on Windows 11 / CPython 3.14.5. It validates
regression work counts and binds the new implementation; it is **not** a physical
layout scalability benchmark. Its 315.4402 ms IR sample is a single diagnostic
observation, not a speedup or cross-machine performance claim. Older evidence is
retained with its original source identity.
