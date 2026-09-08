# Circuit IR version 1

`org.spicetrellis.circuit-ir` is the stable boundary between SpiceTrellis and tools that should
not depend on its parser classes. It represents the analyzed circuit rather than the input token
stream: includes and selected library sections have already been resolved, subcircuit scopes are
explicit, expressions use one canonical structural form, and identities are deterministic.

The normative wire shape is the Draft 2020-12 JSON Schema in
[`schemas/circuit-ir-v1.schema.json`](schemas/circuit-ir-v1.schema.json). The Python decoder also
enforces semantic invariants that JSON Schema cannot conveniently express.

## Producing and checking an artifact

```bash
spice-trellis export-ir circuit.sp --include-root project -o build/circuit.ir.json
spice-trellis check-ir build/circuit.ir.json
```

`export-ir` first performs the same include and semantic analysis as `lint`. It writes nothing if
that analysis has an error. `--compact` selects canonical single-line JSON. `--require-lossless`
also refuses a circuit containing an unsupported card; without that flag, each unsupported card is
retained in `losses` with its text, reason, scope, and source range.

Every file-producing command refuses an existing destination by default and accepts replacement
only with `--force`. An output may never alias the entry deck or any analyzed include, even when
forced. Writes are completed in a same-directory temporary file, flushed, and atomically installed;
the original input and an existing destination therefore survive a failed command.

`check-ir` parses independently and reports the schema version, SHA-256 fingerprint, module,
instance, model, and declared-loss counts. It returns status 1 with `--require-lossless` when the
document is valid but declares a loss, and status 2 when the document itself is invalid.

## Identities and scopes

The first module is always `$top`. Other modules correspond to analyzed `.subckt` definitions.
Instance IDs are case-normalized and globally unique:

```text
$top::xinput
gain_stage::mload
```

Models carry both a scope and an ID, because a model name can be local to a subcircuit:

```text
gain_stage::model::nch
```

An ID is derived from the enclosing module and source name; a decoder rejects a document whose ID
does not equal that derivation. Module, instance, model, port, parameter, global-node, and
dependency uniqueness rules are checked using the same case behavior as the relevant SPICE
symbols. This prevents a consumer from receiving two plausible meanings for one reference.

Symbol identifiers use the documented portable-SPICE ASCII grammar. Ports, connections, and
global nodes are non-empty printable ASCII tokens; punctuation commonly used in hierarchical and
bus nodes remains valid. Keeping identity-bearing tokens ASCII gives independent implementations
the same case-folding behavior instead of inheriting language-specific Unicode rules. Free-form
source syntax and diagnostic reasons remain UTF-8.

Every subcircuit module, parameter declaration, model, instance, instance override, and declared
loss carries a source range. `$top` alone has a null module source because it is assembled from the
resolved entry stream rather than declared by one `.subckt` card. Every non-null source file must
belong to `dependencies`; provenance cannot point at an undeclared side input.

Source ranges are one-based and half-open: `start` is inclusive and `end` is exclusive. Columns
count Unicode scalar values, not UTF-8 bytes or user-perceived grapheme clusters. A single-line card
containing 10 scalar values therefore spans `[1, 1]` through `[1, 11]`. A logical card continued
across physical lines starts at the first physical line and ends immediately after the final scalar
value of the last physical line; its physical segments use the same convention in the Python
syntax model.

Source paths contain printable ASCII, use normalized relative POSIX spelling, and are unique
ignoring ASCII case so one document cannot alias files on a case-insensitive checkout. Line and
column values are limited to `1..2147483647`, the common cross-language signed 32-bit range.

The `source_form` fields retain syntax that has no portable structured representation yet. They are
evidence for a downstream dialect adapter, not permission to execute text.

## Reproducible source names

Absolute checkout paths are never serialized. Files beneath the entry deck directory use
normalized relative POSIX paths. Bytes outside the printable portable path subset (including
Unicode and spaces) are UTF-8 percent-encoded, so the logical path remains stable and ASCII on
every consumer. An explicitly allowed include outside that directory uses:

```text
external/<sha256-of-source-bytes>/<basename>
```

The source snapshot captured during analysis supplies the digest. Consequently, copying the same
project and external library to another directory produces byte-identical IR. A missing snapshot
for an external dependency is an error instead of falling back to a machine-specific path.

The circuit fingerprint is SHA-256 over canonical JSON: object keys are sorted, insignificant
whitespace is absent, and UTF-8 is used directly. It identifies the represented circuit and its
declared losses; it is not a signature or a statement that the input is electrically correct.

## Strict decoding

The decoder rejects:

- unknown or missing fields, duplicate JSON object keys, non-finite numbers, and unsupported
  schema versions;
- booleans where integers are required, invalid or non-canonical expressions, reversed source
  ranges, and non-canonical IDs;
- absolute, parent-traversing, backslash-separated, or otherwise non-normalized source paths;
- duplicate or foreign-scoped modules, models, instances, parameters, ports, nodes, and
  dependencies;
- inconsistent family fields or terminal counts, unresolved MOS models, unresolved/arity-mismatched
  subcircuit instances, and duplicate per-instance parameters;
- an entry absent from the dependency closure, a source range outside that closure, or a declared
  loss naming an absent module;
- input larger than 4 MiB, more than 4,096 modules, more than 1,000,000 instances, or oversized
  subordinate collections.

The loader reads at most 4 MiB plus one byte before rejecting an oversized file. Strings must
contain Unicode scalar values and U+FFFD is excluded so JSON decoders cannot turn a lone surrogate
escape into a different accepted value. Expression atoms are ASCII; numeric literals are at most
128 characters, literal exponents are limited to magnitude 10,000, and one expression may contain
at most 1,024 lexical tokens and 512 AST nodes. Expression nesting is limited to 256 levels.
“Canonical expression” here means canonical operator/bracing structure. Atom spelling such as
`1k` versus `1K`, and parameter name case, remains evidence-preserving and therefore remains
fingerprint-significant.

The limits protect service integrations from accidentally treating a JSON decoder as an unbounded
work queue. They do not replace an operating-system sandbox for hostile public uploads.

## Python API

```python
from spicetrellis import analyze_file, build_ir, dump_ir, load_ir

analysis = analyze_file("circuit.sp")
if not analysis.has_errors:
    circuit = build_ir(analysis)
    print(circuit.fingerprint)
    print(dump_ir(circuit))

saved = load_ir("build/circuit.ir.json")
```

`CircuitIR` and every nested record are immutable dataclasses. `dump_ir` is deterministic;
`load_ir_text(dump_ir(value))` reconstructs an equal value for every producer-created IR.

The [`interop/go`](../interop/go/README.md) module is a separately implemented consumer using only
the Go standard library. It validates the full v1 identity/reference/expression contract and shares
a fingerprint-pinned fixture and adversarial rejection corpus with Python. CI also produces a
multi-file artifact with the Python CLI, consumes it with the Go CLI, and requires identical
fingerprints. This is an executable interoperability check, not generated bindings that could
repeat a producer bug mechanically.

## Compatibility policy

Version 1 is additive only at the package API level, not on the wire. A v1 decoder rejects unknown
fields so a producer cannot silently send new semantics to an old consumer. A future incompatible
shape will use a new integer `schema_version` and a separate schema file. The top-level `schema`
identifier will remain reserved for SpiceTrellis circuit interchange.

Syntax support and IR support are deliberately different promises. Adding a parser card does not
make it structured IR automatically: until a reviewed mapping exists, the card remains a declared
loss. This preserves evidence and keeps interoperability claims narrower than the implementation.
