# Validation assets

The `corpus/portable_analog` decks are clean-room, MIT-licensed fixtures. They model common
hierarchical and passive front-end shapes without simulation-accurate device models or PDK data.
Their exact hashes and provenance are recorded in `benchmarks/manifest.json`.

`spice-trellis fuzz-smoke FILE --cases 128 --seed 0` performs bounded deterministic byte-level
mutations. It is a parser robustness smoke test, not exhaustive fuzzing or electrical validation.
Repeating the same command produces identical work counters.

`spice-trellis inventory FILE --interop` emits version 1 of
`org.spice-tools.structural-summary`. File records are bound to their bytes with SHA-256. This
summary is observational: consumers must parse and validate the artifact independently.

The portable Draft 2020-12 contract is published at
`docs/schemas/structural-summary-v1.schema.json`. JSON Schema describes the wire shape; the
producer additionally enforces case-insensitive path uniqueness and equality between the file
record count and the reported structural count.

File-backed analysis is bounded by `AnalysisLimits`: 2 MiB per unique file, 10 MiB total, 256
unique files, 64 include levels, and 250,000 expanded statements by default. Reads stop at the
configured boundary before parsing, repeated failed paths are not reopened, and limit violations
are returned as structured diagnostics (`ST2005` through `ST2010`). Applications may pass a stricter
immutable limit set to `analyze_file(..., limits=AnalysisLimits(...))`.
A `.lib` section call obeys the same include-root confinement and nesting limit as an
`.include`, and the structure of a library file is checked in every file that is expanded
rather than only in files a call opens, so an unclosed section is reported (`ST2014`)
instead of silently discarding the cards that follow it.
Excessively nested in-memory expressions are likewise converted to the `ST1004` diagnostic instead
of exposing a Python recursion failure.
Expression evaluation bounds exponent magnitude at 10,000 to keep a syntactically valid parameter
from requesting unbounded numeric work.

Hierarchy flattening is independently bounded by `ElaborationLimits`: 64 instance levels, 10,000
output statements, and 10,000 provenance records by default. Every emitted statement—including
comments and opaque cards—counts against the output budget. Once any limit is reached, traversal
stops immediately and returns an `ST3009`, `ST3010`, or `ST3011` error; the CLI will not write a
partial flattened deck.

Run `python benchmarks/benchmark.py --iterations 25` for the reproducible workload. The result binds
the workload, installed version, actual imported package tree, and benchmark harness. Content hashes and element counts are
regression contracts; timing is comparable only in a controlled environment with the same source
identity.
