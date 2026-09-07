# SpiceTrellis

[![CI](https://github.com/appleweiping/SpiceTrellis/actions/workflows/ci.yml/badge.svg)](https://github.com/appleweiping/SpiceTrellis/actions/workflows/ci.yml)
[![CodeQL](https://github.com/appleweiping/SpiceTrellis/actions/workflows/codeql.yml/badge.svg)](https://github.com/appleweiping/SpiceTrellis/actions/workflows/codeql.yml)
[![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

SpiceTrellis is a provenance-preserving front end for a documented,
portable subset of SPICE. It reads hierarchical circuit decks, follows safe
local includes, reports syntax and semantic problems together, produces a
stable inventory, and expands subcircuit instances into a deterministic flat
deck with a machine-readable source map.

The project does not run a simulator. It is intended for tooling that needs to
understand a deck before choosing how or where to simulate it.

## Current capabilities

- Physical-line continuation with leading `+` and source ranges spanning every
  contributing line.
- Full-line `*` comments and `$`/`;` inline comments outside quoted strings,
  parameter braces, and parentheses.
- Primitive `R`, `C`, `L`, `V`, `I`, and `M` cards plus hierarchical `X`
  instances.
- `.include`, `.param`, `.subckt`, `.ends`, `.model`, `.global`, and `.end`.
- `.lib FILE SECTION` corner selection, with `.lib SECTION` / `.endl` section
  definitions; only the named section is inlined.
- A deliberately small arithmetic expression language with parameter names,
  `+`, `-`, `*`, `/`, integer powers, parentheses, braces, and common SPICE
  scale suffixes.
- Include-root confinement, include- and section-cycle detection,
  library-section structure checks, duplicate-symbol checks,
  parameter dependency analysis, subcircuit arity checking, and recursive-call
  detection.
- Deterministic flattening with hierarchical element/node names and a source
  map that records both definition sites and instance expansion chains.
- Stable text and JSON diagnostics suitable for local scripts and CI.
- No runtime Python dependencies.

Unknown directives and element families are preserved as opaque cards and
reported as warnings. This makes unsupported syntax visible instead of
silently changing a circuit.

## Installation

SpiceTrellis requires Python 3.11 or newer.

```bash
python -m pip install .
spice-trellis --version
```

For development:

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src
bandit -q -r src
pytest --cov=spicetrellis --cov-report=term-missing
python -m build
```

## Quick start

The repository contains an entirely synthetic hierarchical passive-filter
example. It uses no PDK, model library, or proprietary data.

![Real SpiceTrellis CLI inventory and diagnostics](docs/assets/demo.svg)

```bash
spice-trellis lint examples/hierarchical_filter/top.sp
spice-trellis inventory examples/hierarchical_filter/top.sp
spice-trellis flatten examples/hierarchical_filter/top.sp \
  --output build/filter.flat.sp \
  --provenance build/filter.map.json
```

The flat deck contains primitive cards such as:

```spice
Xfilter__Xfirst__Rseries in Xfilter__n__middle 2000
Xfilter__Xfirst__Cshunt Xfilter__n__middle 0 0.000000001
```

Names encode the instance path, but source-map entries retain the original
subcircuit definition and every expansion site. Generated names are stable for
the same input and do not depend on Python hash iteration order.

## Asking the source map questions

Flattening writes a source map. Until now it was only a file: a caller who
wanted to use it had to load it and search it themselves, which is the work the
map was supposed to save.

**Backwards**, when a simulator blames a card and you need the line that
produced it:

```console
spice-trellis locate top.sp Xfilter__Xsecond__Cshunt --include-root .
```

```
Xfilter__Xsecond__Cshunt (card 8)
  expanded at .../top.sp:5:1
    expanded at .../cells/passive stages.sp:10:1
      defined at .../cells/passive stages.sp:4:1
```

The answer is a chain, not a line. A subcircuit instantiated twice produces two
cards from one definition, and the definition alone does not say which copy
failed; the expansion sites do.

**Forwards**, when you are about to edit a line and need to know what it
becomes:

```console
spice-trellis locate top.sp --source 'cells/passive stages.sp:3' --include-root .
```

```
cells/passive stages.sp:3 produced 2 card(s):
  Xfilter__Xfirst__Rseries (card 5)
  Xfilter__Xsecond__Rseries (card 7)
```

A line that produces nothing reports that plainly rather than failing: a
statement inside a subcircuit nothing instantiates reaches no card, and that is
usually the thing worth knowing.

**A whole subtree**, when the blame lands on an instance rather than a card:

```console
spice-trellis locate top.sp --under Xfilter --include-root .
```

Every physical line of a continued statement resolves to the same card, since a
reader pointing at any of them means the same statement. A card is looked up by
name first and only then by output index, because a deck may legitimately
contain a card named with digits and a lookup should not depend on what else is
in the deck.

`--json` writes the same answers as a document. `spicetrellis.provenance` also
rebuilds an index from a map that was written out and read back, so a saved map
answers exactly what a fresh one does.

The intentionally broken example demonstrates multi-error reporting:

```bash
spice-trellis lint examples/hierarchical_filter/broken.sp
```

## CLI reference

The command line exposes six subcommands, each taking exactly one input path as
its only positional argument:

- `parse`: syntax JSON for a single file.
- `lint`: include-following analysis and diagnostics.
- `flatten`: deterministic hierarchy expansion.
- `format`: canonical rendering of a single file.
- `inventory`: structural summary of an analyzed project.
- `fuzz-smoke`: bounded deterministic parser mutation counters.

`parse`, `lint`, `inventory`, and `fuzz-smoke` always write to standard output.
`flatten` and `format` write to standard output unless `-o`/`--output` names a
file. `lint` renders diagnostics as its result and `parse` embeds them in its
JSON; `flatten`, `format`, and `inventory` instead write blocking diagnostics to
standard error and produce no artifact. `--include-root` is accepted by the
three commands that follow includes (`lint`, `flatten`, and `inventory`) and may
be repeated.

`spice-trellis <command> --help` prints the options for one subcommand.

```bash
spice-trellis --version
```

```text
spice-trellis 0.3.0
```

### Parse one file

```bash
spice-trellis parse circuit.sp
```

Produces JSON for the syntax tree and attached source spans. It does not follow
includes.

### Analyze a project

```bash
spice-trellis lint circuit.sp
spice-trellis lint circuit.sp --json
spice-trellis lint project/circuit.sp --include-root project --include-root shared
```

The entry file's directory is permitted automatically. Additional include
roots must be supplied explicitly. Resolved include paths outside all roots are
errors, including paths that escape through a symbolic link.

Every problem is reported with a stable code and a source location. The command
exits 1 when any diagnostic is an error:

```bash
spice-trellis lint examples/hierarchical_filter/broken.sp
```

```text
examples\hierarchical_filter\broken.sp:1:1: error ST2003: include cycle detected: broken.sp -> cycle_a.sp -> broken.sp
examples\hierarchical_filter\broken.sp:3:1: error ST2101: duplicate parameter 'repeated'
examples\hierarchical_filter\broken.sp:4:1: error ST2207: unknown subcircuit 'absent_cell'
```

Each line is prefixed with the resolved absolute path; the prefix is shortened
above. A clean deck prints `no diagnostics` and exits 0. `--json` emits the same
diagnostics as a structured array instead.

### Flatten hierarchy

```bash
spice-trellis flatten circuit.sp
spice-trellis flatten circuit.sp -o circuit.flat.sp --provenance circuit.map.json
spice-trellis locate circuit.sp CARD_NAME
spice-trellis locate circuit.sp --source FILE:LINE
spice-trellis locate circuit.sp --under INSTANCE/PATH
```

Flattening is refused when analysis contains an error. SpiceTrellis never emits
a knowingly partial flat deck. Parameter values consumed by supported cards are
evaluated before output.

### Format one file

```bash
spice-trellis format circuit.sp
spice-trellis format circuit.sp --check
spice-trellis format circuit.sp --output canonical.sp
```

`--check` returns status 1 when canonical output differs from the input and
does not modify the file.

### Inventory

```bash
spice-trellis inventory circuit.sp
spice-trellis inventory circuit.sp --interop
```

The JSON result includes source-file and include counts, subcircuit pin lists,
element-family counts, parameters, and models. `--interop` replaces it with the
versioned `org.spice-tools.structural-summary` document, which adds a producer
record and a SHA-256 for every contributing file, reports case-folded parameter
names, and reduces subcircuits to a count. Both forms refuse to emit a summary
for a deck that contains errors.

### Robustness smoke test

```bash
spice-trellis fuzz-smoke examples/hierarchical_filter/top.sp
spice-trellis fuzz-smoke circuit.sp --cases 512 --seed 7
```

```text
{
  "bytes_examined": 25240,
  "cases": 128,
  "diagnostics": 48
}
```

Bounded deterministic byte-level mutations are parsed in memory and the work is
reported as counters. `--cases` defaults to 128 and must be between 1 and 10000;
`--seed` defaults to 0. The same file, case count, and seed always produce the
same counters. This is a parser robustness check, not exhaustive fuzzing and not
electrical validation.


## Library sections and corners

A PDK ships one library file holding several corners, and a deck selects one by
name:

```spice
* inverter, typical corner
.lib "corners.lib" tt
M1 out in vdd vdd pch w=2u l=0.18u
M2 out in 0   0   nch w=1u l=0.18u
.end
```

```spice
* corners.lib
.lib tt
.model nch nmos level=1 vto=0.50
.endl tt

.lib ff
.model nch nmos level=1 vto=0.42
.endl ff
```

Only the named section is inlined. Section names are matched without regard to
case, one file may supply two different sections to the same deck, and a section
may itself `.include` a file or call another section. A section is inert until
something calls it, so `.include`-ing a library file wholesale contributes none
of its corners -- which is the point of choosing one by name.

The one-argument `.lib FILE` form that some dialects read as "include this whole
file" is deliberately not accepted, because other dialects read the same card as
the opening of a section. Guessing between them would silently change which
device models a circuit is built from. SpiceTrellis reads a single argument as a
section opening and reports `ST2014` when no `.endl` closes it, so a deck
written in the other dialect gets a clear error rather than a deck whose
remaining cards have quietly disappeared. Use `.include` for a whole file.

| Code | Meaning |
|---|---|
| `ST2011` | the named section is not defined in that file; the message lists the ones that are |
| `ST2012` | a section name is defined more than once in one file |
| `ST2013` | `.endl` has no open section, or names a different one |
| `ST2014` | a section is never closed by `.endl` |
| `ST2015` | a section opens inside another; sections do not nest |
| `ST2016` | a section call cycle |

Library paths obey the same include-root confinement as `.include`, and a
section call counts against the same nesting limit.

## Python API

```python
from spicetrellis import analyze_file, flatten, format_deck, inventory, parse_text

syntax = parse_text("R1 input 0 10k\n.end\n", filename="memory.sp")

analysis = analyze_file("examples/hierarchical_filter/top.sp")
if not analysis.has_errors:
    flat = flatten(analysis)
    print(format_deck(flat.statements))
    print(inventory(analysis).as_dict())
```

Input problems are represented by immutable diagnostics. Each diagnostic has a
stable code, severity, message, primary source range, and optional related
ranges.

## Portable-SPICE expression subset

Expressions may be bare or enclosed in braces:

```spice
.param base=1k ratio=2
R1 a b {base*ratio}
```

Supported scale suffixes are `t`, `g`, `meg`, `k`, `m`, `mil`, `u`, `n`, `p`,
and `f`. As in traditional SPICE notation, `m` means milli and `meg` means
mega. Arbitrary functions, conditionals, Python syntax, and user-defined code
are rejected. Exponents must be integers.

Parameter lookup is case-insensitive. Original identifier spelling is retained
for presentation. During subcircuit expansion, explicit instance overrides
take precedence over subcircuit defaults, which take precedence over global
values. A nested caller passes a local value through an explicit override.

## Exit codes

- `0`: command completed without error diagnostics.
- `1`: readable input contained syntax or semantic errors, or `format --check`
  detected a difference.
- `2`: command-line usage, file access, or text-decoding failure.

Warnings do not change the exit status, but they are always rendered.

## Security properties

SpiceTrellis reads text and writes explicitly requested artifacts. It does not:

- access the network;
- invoke a shell or simulator;
- evaluate Python code;
- load native plugins;
- follow an include outside configured roots.

Circuit text is untrusted input. Built-in file, byte, include-depth, statement,
expression, and elaboration budgets fail closed, but they are not an operating-system
sandbox. Callers handling public uploads should also enforce process-level CPU and
memory limits. See [SECURITY.md](SECURITY.md) for responsible reporting.

## Scope and limitations

Current 0.x releases intentionally do not implement:

- numeric simulation or performance prediction;
- `.lib` section selection, `.control` execution, behavioral expressions, or
  Verilog-A;
- full vendor-specific SPICE dialects;
- PDK model semantics or model-file licensing decisions;
- automatic correction of a user's circuit;
- graphical visualization.

Opaque cards can be formatted and inspected, but SpiceTrellis makes no claim
that it understands their electrical meaning. A supported `X` instance must be
fully resolved before flattening.

## Determinism and provenance

The architecture separates syntax, semantic resolution, and elaboration.
Source spans are attached before parsing and survive every later phase. Symbol
tables use case-folded keys for lookup and stable sorted order for diagnostics.
Output order follows source and instance order rather than hash-table order.

See [docs/architecture.md](docs/architecture.md) for data-flow and invariants.

## Validation and interoperability

The clean-room portable analog corpus supports deterministic fuzz and benchmark entry points. See
[docs/validation.md](docs/validation.md) for provenance and reproduction commands.
`spice-trellis inventory deck.sp --interop` emits a content-bound structural observation for
independent consumers; it is not proof of electrical correctness.

## Contributing

Contributions are welcome when they include a documented syntax boundary,
tests for valid and invalid inputs, and deterministic output. Read
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request.

SpiceTrellis is released under the [MIT License](LICENSE).
