# Changelog

All notable changes to this project will be documented in this file. The format
follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

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
