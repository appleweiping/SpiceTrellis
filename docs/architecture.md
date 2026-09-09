# Architecture

## Design goals

SpiceTrellis has four primary invariants:

1. User text is never executed.
2. Unsupported text remains visible.
3. An emitted flat element can be traced to its definition and instance path.
4. Identical bytes and configuration produce identical diagnostics and output.

The implementation is intentionally split into phases with immutable values at
their boundaries.

The independent physical-view boundary uses `geometry.py` for exact database-unit
isometries/intersection/containment, `physical.py` for technology and multi-view
cell validation plus preflighted hierarchy materialization, and `physical_json.py`
for strict decimal-string 64-bit interchange. It never infers electrical
connectivity from labels or coincident shapes. See [its contract](physical-library.md).

```text
UTF-8 files
    |
    v
physical lines -- source ranges --> logical cards
    |
    v
scanner/parser --> syntax decks + recoverable diagnostics
    |
    v
include loader --> confined, ordered project stream
    |
    v
semantic builder --> symbols, parameter graph, call graph
    |
    +--> inventory / versioned circuit IR
    |
    v
elaborator --> primitive cards + provenance records
    |
    v
canonical SPICE / stable JSON
```

## Source normalization

`source.logical_cards` is the only phase that joins physical lines. Every
logical card records an aggregate span and the individual physical segments.
Comment splitting is stateful: markers inside quotes, braces, or parentheses
remain circuit text.

No later phase reconstructs source positions from normalized strings.

## Syntax layer

`parser.parse_text` recognizes the portable subset and returns a `SyntaxDeck`.
Card-level parse failures become `Opaque` statements plus diagnostics, allowing
the parser to continue at the next logical card. Unknown but well-formed card
families are also opaque, with warning rather than error severity. `.end` is a
hard syntax boundary: non-trivia that follows it is diagnosed and excluded.

Expressions use a dedicated Pratt parser. The evaluator accepts only immutable
expression nodes and decimal parameter values; it contains no dynamic dispatch
to user functions and never calls `eval`.

## Include confinement

Includes are resolved relative to the including file, then canonicalized. A
candidate must remain within at least one configured root after resolution.
This check also covers symbolic-link escapes. The loader caches parsed syntax
decks but expands a file each time it is included, retaining source-order
semantics. The active path stack detects include cycles.

## Semantic layer

The semantic builder first extracts subcircuit scopes, then constructs symbol
tables. This permits forward references while still reporting duplicate
definitions. It checks:

- matching `.subckt`/`.ends` boundaries;
- duplicate subcircuits, formal pins, and element names;
- `X` target existence and pin arity;
- recursive subcircuit call components;
- duplicate, undefined, and cyclic parameters.

Parameter and call cycles use strongly connected components. Diagnostics are
sorted only for presentation; circuit statement order is never sorted.

## Elaboration

Elaboration starts only after error-free semantic analysis. For each `X`
instance it builds:

- a formal-pin to actual-node map;
- a child parameter environment;
- a hierarchical name prefix;
- an expansion chain of source spans.

The complete parameter scope is resolved as a dependency graph before any
element in that scope is emitted, so a valid forward `.param` reference has the
same meaning during analysis and elaboration.

Internal nodes receive names derived from the full instance path. Ground and
declared global nodes are never renamed. Primitive values and supported
parameter assignments are evaluated to deterministic decimal text. A failure
to evaluate becomes an error, never a guessed value.

Every emitted primitive has one `Provenance` record containing its output
index, generated name, definition span, and ordered instance spans.
Hierarchical name segments use a reversible underscore/byte escape before the
`__` path delimiter is added. Consequently, a literal delimiter inside one
source name cannot alias a multi-level path; a final collision guard still
turns any duplicate output name into an error.

## Output stability

Canonical SPICE retains source statement order and normalizes whitespace.
Stable JSON uses sorted object keys. Diagnostics are ordered by normalized file
name, source position, severity, code, and message. Tests run selected commands
under different hash seeds to guard against accidental dependence on mapping
iteration.

## Public boundaries

`spicetrellis.api` exports parsing, analysis, elaboration, formatting, inventory, deterministic
mutation smoke testing, structural-summary operations, and the versioned circuit IR. The structural
summary is emitted only after error-free semantic analysis and contains shared observations plus
content digests; it does not grant trust to downstream consumers. Circuit IR is a stricter
language-neutral boundary with explicit hierarchy and declared conversion losses; its complete
contract is documented in [`circuit-ir.md`](circuit-ir.md).

- `parse_text`
- `analyze_file`
- `flatten`
- `format_deck`
- `inventory`
- `fuzz_smoke`
- `structural_summary`
- `build_ir`, `dump_ir`, `load_ir`, `load_ir_text`, `write_ir`

The CLI is a thin adapter over these functions. It owns process exit codes and protects analyzed
input paths from output aliases. The `write_ir` library boundary shares the atomic no-clobber
writer and exposes replacement only through `force=True`; library functions never terminate the
process.
