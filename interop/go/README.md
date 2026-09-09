# Go interoperability

This directory contains independent, zero-dependency Go 1.23+ consumers for SpiceTrellis Circuit
IR version 1 and physical-library JSON version 1. They prove that both wire contracts are usable
without importing Python parser classes. Neither package is a SPICE parser or simulator.

```go
document, err := circuitir.Load("circuit.ir.json")
if err != nil {
    return err
}
summary, err := document.Summary()
```

The command-line checker is equally small:

```bash
go run ./cmd/circuitir-check ../../../build/circuit.ir.json
go run ./cmd/circuitir-check --require-lossless ../../../build/circuit.ir.json
```

Status 0 means valid, status 1 means valid but disallowed by `--require-lossless`, and status 2
means usage, I/O, JSON, or contract validation failed.

## Physical-library JSON

`physicalir` strictly decodes the complete raw physical-library v1 document, including exact
signed/unsigned 64-bit database-unit fields, technology layers, cells, raw layout, abstract
views, ports, instances, annotations, and all three geometry variants:

```go
library, err := physicalir.Load("physical-library.json")
if err != nil {
    return err
}
for _, cell := range library.Cells {
    // Consume validated raw views without Python.
    _ = cell.Layout
}
```

The corresponding checker reports the decoded library identity and raw layer, cell, and stored
`Shape` record counts together with the SHA-256 of the exact input bytes. Shape records include
layout, blockage, and port-access shapes; the abstract outline polygon is not a `Shape` record:

```bash
go run ./cmd/physicalir-check ../../../build/physical-library.json
```

Status 0 means that the whole document decoded and passed semantic validation; status 2 means
usage, I/O, JSON, geometry, reference, hierarchy, or resource validation failed. The input digest
is not a canonical fingerprint: it is directly comparable only for byte-identical files.

The physical package deliberately does not flatten hierarchy, infer connectivity from coincident
geometry, run design-rule checks, extract parasitics, or claim foundry/PDK semantics. Python owns
those higher-level operations when explicitly supported.

Physical JSON decoding rejects duplicate and unknown members, noncanonical or out-of-range
decimal strings, invalid geometry, broken layer/cell/port references, cycles, excessive hierarchy
depth, and aggregate item/point/text/polygon-work limits. Geometric predicates and areas use
`math/big`, so valid signed-64 coordinates cannot overflow intermediate calculations.

Python and Go both execute the positive documents and 20-case rejection corpus under
`../fixtures/physical-v1/`; this pins the shared wire semantics without treating JSON Schema as a
substitute for semantic validation.

## Circuit IR validation parity

The Go decoder independently checks:

- the exact required field set at every object and duplicate JSON keys before struct decoding;
- UTF-8, a 4 MiB read limit, a 512-level nesting ceiling, and collection limits;
- normalized relative source paths, one-based half-open ranges whose columns count Unicode scalar
  values, and dependency closure;
- portable identifiers and tokens, case-insensitive uniqueness, canonical IDs, and model scopes;
- all seven v1 families, their field/terminal shapes, MOS model closure, and X target arity;
- canonical nested expressions using a separate implementation of the documented grammar;
- explicit loss scope and deterministic canonical JSON fingerprinting.

The shared `../fixtures/minimal-v1.json` document has fingerprint
`9a37a5fd9b60ffdf5f2277eb5cb3048c7f2acd07a1f9e99cfd5cf314a5927c44` in both implementations.
Tests fail if either language changes that contract accidentally.

## Development

```bash
go test -race ./...
go vet ./...
go test -coverprofile=coverage.out ./...
go tool cover -func=coverage.out
```

CI repeats race and vet checks on Linux, Windows, and macOS and enforces at least 90% aggregate Go
statement coverage on Linux from the profile's raw covered and total statement counts, without
rounding the displayed percentage. The module uses only the Go standard library; `go.mod` is
therefore the complete dependency inventory.

The Circuit IR implementations execute `../fixtures/rejection-corpus-v1.json`. It pins ambiguous
wire cases that historically diverged between standard-library decoders: dot and case-aliased paths,
out-of-range coordinates, non-ASCII numeric atoms and whitespace, Unicode case-folded suffixes,
oversized exponents, lone surrogate escapes, and the replacement character. CI additionally emits
a multi-file artifact with the Python CLI and checks its full fingerprint with this Go consumer.

Source coordinates are restricted to `1..2147483647`; source paths are printable ASCII and unique
ignoring ASCII case. Expressions use ASCII atoms, at most 128 characters per numeric literal,
1,024 lexical tokens, 512 AST nodes, 256 nesting levels, and literal exponent magnitude at most
10,000. Free-form strings reject U+FFFD so Go cannot normalize a lone JSON surrogate into an
accepted value that Python reads differently.

## Module versions

The nested module follows Go's repository-subdirectory tag convention. Python release `vX.Y.Z`
and signed Go tag `interop/go/vX.Y.Z` must point to the same commit on `main`; a dedicated workflow
verifies the signature, ancestry, version match, race detector, vet, and coverage before that Go
version is considered published. Consumers can then use:

```bash
go get github.com/appleweiping/SpiceTrellis/interop/go@v0.6.0
```
