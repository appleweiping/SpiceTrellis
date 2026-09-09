# Simulator-result ingestion (development)

This feature branch adds a process-independent result reader. It never starts a
simulator and never executes a rawfile's `Command` metadata. A parsed artifact
proves only that the data satisfy its format contract, not that the simulator
converged or exited successfully. The caller must retain process/solver evidence.

## Raw profile

`spicetrellis.raw_results.parse_raw(bytes)` accepts rectangular SPICE raw plots
with real or complex values, original vector order, metadata, and appended plots.
`load_raw(path)` reads at most the caller's byte ceiling plus one. Results retain
the exact source SHA-256, plot titles and analysis names, variable names and
declared quantities. A quantity is not a guessed physical unit. Values use
binary64 floats or pairs of binary64 values for complex samples; numeric
overflow, non-finite values and nonzero decimal underflow are errors.

Binary raw data do not declare byte order. `byte_order="little"` or `"big"` is
therefore mandatory for binary blocks. The reader never guesses from plausible
values or from the host on which parsing happens. ASCII blocks need no byte order.

Display-only `grid=N` metadata on vectors is retained without inferring logarithmic
sampling or changing values. Global dimensions must multiply to the declared
point count. Vector-specific dimensions, unequal-length/unpadded vectors and
fast-access layouts are currently rejected instead of silently reshaped.

Default limits are 64 MiB of source bytes, 128 plots, 4,096 total variables,
2,000,000 total points, 2,000,000 total cells, and 64 KiB per header/value line.
Typed immutable limits permit a bounded larger profile or a stricter caller
profile. All counts are checked before data allocation. Header commands and
unknown header metadata remain evidence only; duplicate fields and unsafe names
are rejected. Public immutable result constructors also check shapes and values.

The format boundary follows the
[ngspice rawfile documentation](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)
and [Xyce output documentation](https://xyce.sandia.gov/documentation-tutorials/).
These references define interchange formats, not an implementation dependency.

## Initial real-simulator check

An original RC network with R = 1 kOhm and C = 1 uF was run with ngspice 42 on
Linux. The local development check read an operating point, 33 complex AC points,
and 529 transient points in both ASCII and binary forms. OP matched exactly;
the maximum AC error against `1 / (1 + j*2*pi*f*R*C)` was 2.24e-16. Both transient
forms agreed with the delayed step response within 2.87e-6 V. These are executed
development measurements, not a public release or a complete Xyce validation.

The repository-owned harness is `tools/check_ngspice_results.py`. With a real
ngspice executable on PATH and this checkout installed, run:

```bash
python tools/check_ngspice_results.py --ngspice ngspice
```

It starts only the fixed original RC experiment in a temporary directory and
prints the executable version, netlist/harness/result digests and numerical
errors. It also checks 11 DC sweep samples against the exact divider-free DC
response. Parser fixtures elsewhere in tests are explicitly synthetic and are
not presented as simulator runs.

## Xyce CSV profile

`load_xyce_csv(path, analysis="ac")` and `parse_xyce_csv(bytes, analysis="ac")`
read an unquoted, comma-delimited rectangular print table. The analysis name is
caller-declared and marked as such in metadata: it cannot be recovered reliably
from a CSV file alone. The default title is also supplied by the reader rather
than inferred to be a simulator title. UTF-8 blank lines and padded labels are
accepted; every nonempty data row must contain the exact header column count
of finite decimal binary64 samples.

Xyce emits names such as `Re(V(a,b))` without CSV quoting. Commas inside balanced
parentheses/braces remain part of a vector name. Quoted, escaped, square-bracketed,
whitespace-containing or unbalanced expression labels are outside this profile
and fail with `ResultError`. A second header, footer or unsupported format is
not silently skipped.

AC real/imaginary/magnitude/phase columns remain separate real-valued columns.
The reader does not assume a phase unit, combine similarly named vectors or
infer physical units; each CSV quantity is `unknown`. Likewise, repeated times
or reset Index columns remain in file order, not fabricated separate sweeps.
For step-aware interpretation retain the originating request and Xyce step data.
The source-format facts were checked against official Xyce 7.10
`N_IO_OutputterLocal.C`, `N_IO_OutputterTimeCSV.C` and
`N_IO_OutputterFrequencyCSV.C`. The original DC divider, AC projection and finite-rise
RC transient oracle harness has now run against a real source-built Xyce 7.10.

```bash
python tools/check_xyce_results.py --xyce /absolute/path/to/Xyce
```

The executed run produced 11 DC points (maximum error 1.12e-16 V), 33 AC points
with 11 columns (maximum error 4.41e-13 across the explicitly checked projections),
and 8,261 transient points (maximum error 2.23e-9 V). The harness checks the
configured projection meanings; the generic parser still does not infer units.
Transient integration uses explicit `RELTOL=1e-8`, `ABSTOL=1e-12` settings and
is checked against the finite-rise closed-form response, not a zero-rise step.
The default integration settings initially missed the stricter oracle tolerance;
the experiment was rerun with tighter solver tolerances, not a relaxed assertion.
All three cases also round-trip through the result JSON contract.

## Result JSON and public API

The public objects and functions are exported from `spicetrellis`: `ResultLimits`,
`ResultVariable`, `ResultPlot`, `SimulationResult`, `ResultError`, the raw/CSV
loaders, and `dump_result`, `load_result_text`, `load_result`, `write_result`.
Columns are immutable tuples, real samples are Python floats, and complex samples
are Python complex values. JSON encodes each complex sample as `[real, imaginary]`.
It preserves vector order, metadata, shape and the original input digest claim.

```python
from spicetrellis import load_raw, write_result, load_result

result = load_raw("ac.raw", byte_order="little")
write_result(result, "ac.result.json")  # refuses replacement unless force=True
assert load_result("ac.result.json") == result
```

The [version 1 structural schema](schemas/simulation-result-v1.schema.json) is
also packaged in the wheel. Schema validation is necessary but not sufficient:
the reader additionally checks binary64 finiteness/underflow, normalized safe
names, case-insensitive uniqueness, equal column lengths, dimension products,
metadata consistency, UTF-8 encoding and whole-artifact work limits. It rejects
duplicate/unknown object fields, booleans masquerading as numbers and non-finite
constants. RAW/CSV parsers compute `source_sha256` from the exact source bytes.
Manually constructed or JSON-loaded values only carry an unverified digest claim:
check it against the original artifact before treating it as source binding.
Neither a matching digest nor parsing success proves authenticity or convergence.

The writer applies the same caller-selected limits as the reader. It visits
original samples lazily and stops at the output-byte ceiling instead of making
a second full waveform object graph. JSON structural checking uses a depth-sized
iterator stack. Metadata cannot override canonical fields, including through
surrounding whitespace. The default 64 MiB ceiling applies independently to
the source and the serialized result: a compact binary source can exceed it when
expanded to JSON and is rejected until a permitted larger profile is selected.

## CLI

```bash
spice-trellis read-result ac.raw --format raw --byte-order little -o ac.result.json
spice-trellis read-result ac.csv --format xyce-csv --analysis ac -o ac.result.json
spice-trellis check-result ac.result.json
```

No format guessing is performed. Commands accept `--max-bytes`, `--max-plots`,
`--max-variables`, `--max-points`, `--max-cells` and `--max-line-bytes` within
hard ceilings. A successful conversion/check exits 0; malformed, unsupported or
over-budget input exits 2. These statuses do not certify solver convergence.
Output files are atomic and no-clobber by default, `--force` permits replacement,
and an output can never alias the input even with `--force`.

The current development suite passes 434 tests with 95.23% branch-aware coverage
on Windows/Python 3.14. A freshly built wheel, built from the source distribution,
was separately installed into a clean Python 3.11 environment: its packaged schema,
CSV digest/JSON round trip and isolated CLI help checks passed. These local checks
are not a completed cross-platform release gate. Independent final review and
remote CI, including the real-ngspice oracle job, remain pending before release.
Additional rawfile layouts and other simulator formats remain explicitly unsupported.
