# Circuit IR for Go

This directory is an independent, zero-dependency Go 1.23+ consumer for SpiceTrellis Circuit IR
version 1. It proves that the wire contract is usable without importing Python parser classes.
It is not a SPICE parser and does not run a simulator.

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

## Validation parity

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

Both implementations execute `../fixtures/rejection-corpus-v1.json`. It pins ambiguous wire cases
that historically diverged between standard-library decoders: dot and case-aliased paths,
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
go get github.com/appleweiping/SpiceTrellis/interop/go@v0.4.0
```
