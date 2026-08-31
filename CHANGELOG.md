# Changelog

All notable changes to this project will be documented in this file. The format
follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

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
