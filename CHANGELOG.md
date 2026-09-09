# Changelog

All notable changes to this project will be documented in this file. The format
follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

## [0.5.0] - 2026-09-08

### Added

- Process-independent simulator result values with exact source digests, real/complex
  columns, explicit dimensions, bounded metadata and immutable shape validation.
- Rectangular SPICE raw readers for ASCII and explicitly endian-selected binary data,
  including appended plots, declared quantities and vector display-grid metadata.
- A conservative Xyce CSV profile that preserves balanced expression headers such as
  `Re(V(a,b))`, real projection columns and original file order. Analysis is explicitly
  caller-declared; physical units, complex recombination and step grouping are not guessed.
- Version 1 of `org.spicetrellis.simulation-result`, its packaged Draft 2020-12 schema,
  strict UTF-8 JSON reading, lazy bounded JSON writing and atomic protected outputs.
- Public Python result APIs, `read-result` and `check-result` commands, complete-artifact
  resource limits and memory-amplification regressions. Original real-simulator oracle
  harnesses exercise ngspice OP/DC/AC/transient ASCII/binary artifacts and Xyce DC,
  complex AC projections and finite-rise transient CSV data with JSON round trips.

### Security

- Release workflows now bind signed annotated tags to GitHub-verified commits on protected
  `main`, require the exact successful main-push CI run, re-test the frozen source archive,
  and bind file-level SPDX evidence to the installed wheel before publication.

## [0.4.0] - 2026-09-07

### Added

- Version 1 of `org.spicetrellis.circuit-ir`, an immutable language-neutral hierarchy with scoped,
  canonical instance and model identities, normalized source locations, canonical expressions,
  deterministic fingerprints, and explicit records for every unsupported card.
- `export-ir` and `check-ir` CLI commands, including a `--require-lossless` policy gate and compact
  deterministic serialization.
- A public Draft 2020-12 schema and a strict independent decoder with duplicate-key, non-finite
  number, malformed identity, ambiguous scope, non-portable path, resource-limit, and deep-input
  rejection.
- Checkout-independent naming for both project files and content-addressed external includes.
- A zero-dependency Go 1.23 consumer and checker with independent canonical-expression parsing,
  model/subcircuit reference closure, cross-language fingerprint fixture, race tests, vet, and an
  enforced 90% statement-coverage floor across Linux, Windows, and macOS CI.
- A shared adversarial rejection corpus and an end-to-end CI gate that produces an artifact with
  Python and independently consumes and fingerprints it with Go. The normative schema is shipped
  in both source and wheel distributions.
- A deterministic 5,000-instance Circuit IR build/serialization benchmark with source, workload,
  artifact-size, and performance-budget evidence.
- DCO verification for every pull-request commit, cryptographic release-tag verification, and a
  signed nested-module tag workflow for independently consumable Go versions.

### Fixed

- Canonical nested binary expressions can be parsed again, so formatting an expression and then
  decoding it is a true round trip rather than failing on the formatter's nested braces.
- Python and Go now share the same acceptance boundary for ASCII number atoms, exponent and source
  coordinates, Unicode scalar values, printable source paths, case-insensitive path aliases, and
  bounded expression token/node/depth budgets.
- Source ranges are explicitly one-based and half-open with Unicode-scalar columns; expression
  limit diagnostics retain their actual cause instead of misreporting every limit as nesting.
- Circuit IR source aliases are resolved once rather than once per record, validated immutable IR
  reuses its validation result, and canonical rendering avoids recursive string concatenation.
- Go duplicate-key scanning no longer accumulates attacker-controlled diagnostic paths, preventing
  deep long-key JSON from amplifying a bounded input into quadratic retained memory.
- Go coverage enforcement uses the profile's raw statement counts instead of accepting a
  one-decimal percentage rounded up by `go tool cover`.
- File loading stops at the IR byte boundary, and all file-producing CLI commands now use atomic
  no-clobber output with explicit `--force` while permanently protecting their input files.
- Release assets include a pinned-tool SPDX 2.3 SBOM and verified SHA-256 checksums alongside
  GitHub build provenance attestations.

## [0.3.0] - 2026-09-07

### Added

- `spice-trellis locate`: query the source map instead of loading and searching it by hand.
  Backwards from a flattened card to the definition and every instance site the expansion passed
  through; forwards from a source line to every card it produced; and over a whole instance path
  when the blame lands on a subcircuit rather than a card.
- A card is reported with its full expansion chain rather than only its definition, because a
  subcircuit instantiated twice yields two cards from one definition and the definition alone
  does not say which copy failed.
- A source line that produces no card reports that plainly rather than failing. A statement
  inside a subcircuit nothing instantiates reaches no card, which is usually the thing worth
  knowing.
- Every physical line of a continued statement resolves to the same card, since a reader
  pointing at any of them means the same statement.
- A query is matched as a name before an output index, because a deck may legitimately contain a
  card named with digits and a lookup should not depend on what else is in the deck.
- `spicetrellis.provenance` as a Python API: `build_index`, `index_from_entries`,
  `ProvenanceIndex`, and `Origin`. An index rebuilt from a map that was written out and read back
  answers exactly what a fresh one does.

- Added `.lib FILE SECTION` corner selection and `.lib SECTION` / `.endl` section
  definitions. Only the named section is inlined; a section is inert until called,
  so including a library file wholesale still contributes none of its corners.
  Section names match without regard to case, one file may supply two sections to
  the same deck, and a section may `.include` a file or call another section.
- Added `ST2011` through `ST2016` for an undefined section, a duplicated section,
  a stray or mismatched `.endl`, an unclosed section, nested sections, and a
  section call cycle. Library paths obey the same include-root confinement as
  `.include`, and section calls count against the same nesting limit.
- The one-argument `.lib FILE` form of other dialects is read as a section opening
  and reported as unclosed rather than guessed. Dialects disagree about whether
  that card includes a file or opens a section, and choosing silently would change
  which device models a circuit is built from. Section structure is now validated
  in every expanded file, not only in files a call opens, so a deck that opens a
  section by accident is told so instead of quietly losing every card after it.
- `CircuitInventory` gained a `library_sections` count of resolved section calls.
  The `structural-summary` interop schema is unchanged.

## [0.2.0] - 2026-08-31

### Added

- Clean-room portable analog corpus, deterministic fuzz smoke runner, and reproducible benchmark.
- Versioned, content-bound structural summary for offline interoperability.
- Distribution-derived runtime versioning and source-bound benchmark evidence.
- Bounded file, project, include-depth, and expansion loading with structured failure diagnostics.
- Fail-closed hierarchy depth, flattened output, and provenance resource limits.
- Linear dependency scheduling for forward parameter resolution under large inputs.

## [0.1.0] - 2026-08-31

### Added

- Portable-SPICE logical-card and expression parsers.
- Safe include traversal and structured semantic diagnostics.
- Parameter and subcircuit dependency validation.
- Deterministic hierarchy flattening with provenance JSON.
- Parse, lint, flatten, format, and inventory commands.
- Synthetic examples and cross-platform automated tests.

### Fixed

- Made flattened hierarchy names injective and added a final collision guard.
- Rejected repeated formal pins and statements after the terminal `.end`.
- Aligned forward parameter evaluation and unary/exponent precedence with the
  documented expression semantics.
