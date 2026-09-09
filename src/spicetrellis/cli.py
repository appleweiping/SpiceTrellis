"""Command-line interface for parsing, linting, flattening, and inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spicetrellis._output import validate_output_paths, write_text_atomic
from spicetrellis._version import __version__
from spicetrellis.api import (
    analyze_file,
    build_ir,
    dump_ir,
    flatten,
    format_deck,
    fuzz_smoke,
    inventory,
    load_ir,
    load_raw,
    load_result,
    load_xyce_csv,
    parse_text,
    structural_summary,
    write_result,
)
from spicetrellis.emit import format_diagnostics, to_json
from spicetrellis.model import Analysis
from spicetrellis.provenance import Origin, build_index
from spicetrellis.result_json import dump_result
from spicetrellis.semantics import DEFAULT_ANALYSIS_LIMITS
from spicetrellis.simulation_results import ResultLimits


def _roots(args: argparse.Namespace) -> tuple[Path, ...]:
    return tuple(Path(item) for item in getattr(args, "include_root", []) or [])


def _read(path: Path) -> str:
    limit = DEFAULT_ANALYSIS_LIMITS.max_file_bytes
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"input exceeds the {limit}-byte limit")
    return content.decode("utf-8-sig")


def _write_or_print(
    text: str,
    destination: str | None,
    *,
    force: bool = False,
    protected: tuple[Path, ...] = (),
    prevalidated: bool = False,
) -> None:
    if destination:
        write_text_atomic(
            destination,
            text,
            force=force,
            protected=protected,
            prevalidated=prevalidated,
        )
    else:
        sys.stdout.write(text)


def _analysis_inputs(result: Analysis) -> tuple[Path, ...]:
    return tuple(path for path, _content in result.source_files)


def _command_parse(args: argparse.Namespace) -> int:
    path = Path(args.path)
    deck = parse_text(_read(path), str(path.resolve()))
    sys.stdout.write(to_json(deck))
    return 1 if any(item.severity == "error" for item in deck.diagnostics) else 0


def _command_lint(args: argparse.Namespace) -> int:
    result = analyze_file(args.path, include_roots=_roots(args))
    rendered = to_json(result.diagnostics) if args.json else format_diagnostics(result.diagnostics)
    sys.stdout.write(rendered)
    return 1 if result.has_errors else 0


def _command_flatten(args: argparse.Namespace) -> int:
    result = analyze_file(args.path, include_roots=_roots(args))
    if result.has_errors:
        sys.stderr.write(format_diagnostics(result.diagnostics))
        return 1
    flattened = flatten(result)
    if flattened.has_errors:
        sys.stderr.write(format_diagnostics(flattened.diagnostics))
        return 1
    protected = _analysis_inputs(result)
    outputs = tuple(Path(item) for item in (args.output, args.provenance) if item)
    validate_output_paths(outputs, protected=protected, force=args.force)
    _write_or_print(
        format_deck(flattened.statements),
        args.output,
        force=args.force,
        protected=protected,
        prevalidated=True,
    )
    if args.provenance:
        write_text_atomic(
            args.provenance,
            to_json(flattened.provenance),
            force=args.force,
            protected=protected,
            prevalidated=True,
        )
    return 0


def _command_locate(args: argparse.Namespace) -> int:
    result = analyze_file(args.path, include_roots=_roots(args))
    if result.has_errors:
        sys.stderr.write(format_diagnostics(result.diagnostics))
        return 1
    flattened = flatten(result)
    if flattened.has_errors:
        sys.stderr.write(format_diagnostics(flattened.diagnostics))
        return 1
    index = build_index(flattened)

    if args.source is not None:
        filename, _, raw_line = args.source.rpartition(":")
        if not filename or not raw_line.isdigit():
            sys.stderr.write("--source must be written as FILE:LINE\n")
            return 2
        resolved = str(Path(filename).resolve())
        use = index.by_source(resolved, int(raw_line))
        if args.json:
            _write_or_print(
                to_json(use.as_dict()),
                args.output,
                force=args.force,
                protected=_analysis_inputs(result),
            )
            return 0
        # No card is a real answer, not a failure: a line inside a subcircuit
        # nothing instantiates produces nothing, and that is worth seeing.
        if not use.copies:
            print(f"{args.source} produced no card in the flattened deck")
            return 0
        print(f"{args.source} produced {use.copies} card(s):")
        for produced in use.origins:
            print(f"  {produced.output_name} (card {produced.output_index})")
        return 0

    if args.under is not None:
        path = tuple(part for part in args.under.split("/") if part)
        found = index.under(path)
        if args.json:
            _write_or_print(
                to_json([item.as_dict() for item in found]),
                args.output,
                force=args.force,
                protected=_analysis_inputs(result),
            )
            return 0
        print(f"{args.under} expanded to {len(found)} card(s):")
        for produced in found:
            print(f"  {produced.output_name} (card {produced.output_index})")
        return 0

    origin: Origin | None = index.resolve(args.card) if args.card is not None else None
    if origin is None:
        sys.stderr.write(f"no flattened card named or numbered {args.card!r}\n")
        return 1
    if args.json:
        _write_or_print(
            to_json(origin.as_dict()),
            args.output,
            force=args.force,
            protected=_analysis_inputs(result),
        )
        return 0
    print(origin.describe())
    return 0


def _command_format(args: argparse.Namespace) -> int:
    path = Path(args.path)
    original = _read(path)
    deck = parse_text(original, str(path.resolve()))
    if any(item.severity == "error" for item in deck.diagnostics):
        sys.stderr.write(format_diagnostics(deck.diagnostics))
        return 1
    rendered = format_deck(deck)
    if args.check:
        return 0 if original.replace("\r\n", "\n") == rendered else 1
    _write_or_print(
        rendered,
        args.output,
        force=args.force,
        protected=(path,),
    )
    return 0


def _command_inventory(args: argparse.Namespace) -> int:
    result = analyze_file(args.path, include_roots=_roots(args))
    if result.has_errors:
        sys.stderr.write(format_diagnostics(result.diagnostics))
        return 1
    value = structural_summary(result) if args.interop else inventory(result).as_dict()
    sys.stdout.write(to_json(value))
    return 0


def _command_fuzz(args: argparse.Namespace) -> int:
    stats = fuzz_smoke(_read(Path(args.path)), cases=args.cases, seed=args.seed)
    sys.stdout.write(to_json(stats.as_dict()))
    return 0


def _command_export_ir(args: argparse.Namespace) -> int:
    result = analyze_file(args.path, include_roots=_roots(args))
    if result.has_errors:
        sys.stderr.write(format_diagnostics(result.diagnostics))
        return 1
    circuit = build_ir(result)
    if args.require_lossless and circuit.losses:
        sys.stderr.write(
            f"circuit IR would contain {len(circuit.losses)} declared loss(es); "
            "remove --require-lossless to preserve them in the artifact\n"
        )
        return 1
    _write_or_print(
        dump_ir(circuit, pretty=not args.compact),
        args.output,
        force=args.force,
        protected=_analysis_inputs(result),
    )
    return 0


def _command_check_ir(args: argparse.Namespace) -> int:
    circuit = load_ir(args.path)
    result = {
        "schema": circuit.schema,
        "schema_version": circuit.schema_version,
        "fingerprint": circuit.fingerprint,
        "modules": len(circuit.modules),
        "instances": circuit.instance_count,
        "models": len(circuit.models),
        "losses": len(circuit.losses),
    }
    sys.stdout.write(to_json(result))
    return 1 if args.require_lossless and circuit.losses else 0


def _result_limits(args: argparse.Namespace) -> ResultLimits:
    return ResultLimits(
        max_bytes=args.max_bytes,
        max_plots=args.max_plots,
        max_variables=args.max_variables,
        max_points=args.max_points,
        max_cells=args.max_cells,
        max_line_bytes=args.max_line_bytes,
    )


def _command_read_result(args: argparse.Namespace) -> int:
    limits = _result_limits(args)
    if args.format == "raw":
        if args.analysis is not None:
            raise ValueError("raw input already declares analysis; --analysis is only for xyce-csv")
        result = load_raw(args.path, byte_order=args.byte_order, limits=limits)
    else:
        if args.byte_order is not None:
            raise ValueError("--byte-order is only for binary raw input")
        if args.analysis is None:
            raise ValueError("xyce-csv input requires a caller-declared --analysis")
        result = load_xyce_csv(args.path, analysis=args.analysis, limits=limits)
    if args.output:
        write_result(
            result, args.output, force=args.force, protected=(Path(args.path),), limits=limits
        )
    else:
        sys.stdout.write(dump_result(result, limits=limits))
    return 0


def _command_check_result(args: argparse.Namespace) -> int:
    result = load_result(args.path, limits=_result_limits(args))
    summary = {
        "format": result.format,
        "source_sha256": result.source_sha256,
        "plots": [
            {
                "analysis": plot.analysis,
                "encoding": plot.encoding,
                "variables": len(plot.variables),
                "points": plot.points,
            }
            for plot in result.plots
        ],
    }
    sys.stdout.write(to_json(summary))
    return 0


def _add_result_limits(parser: argparse.ArgumentParser) -> None:
    defaults = ResultLimits()
    for name in (
        "max_bytes",
        "max_plots",
        "max_variables",
        "max_points",
        "max_cells",
        "max_line_bytes",
    ):
        parser.add_argument(
            "--" + name.replace("_", "-"), type=int, default=getattr(defaults, name)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spice-trellis")
    parser.add_argument("--version", action="version", version=f"spice-trellis {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_command = subparsers.add_parser("parse", help="parse one file and emit syntax JSON")
    parse_command.add_argument("path")
    parse_command.set_defaults(handler=_command_parse)

    lint = subparsers.add_parser("lint", help="load includes and report semantic diagnostics")
    lint.add_argument("path")
    lint.add_argument("--include-root", action="append", default=[])
    lint.add_argument("--json", action="store_true")
    lint.set_defaults(handler=_command_lint)

    flatten_command = subparsers.add_parser("flatten", help="expand hierarchical X instances")
    flatten_command.add_argument("path")
    flatten_command.add_argument("-o", "--output")
    flatten_command.add_argument("--provenance")
    flatten_command.add_argument("--force", action="store_true")
    flatten_command.add_argument("--include-root", action="append", default=[])
    flatten_command.set_defaults(handler=_command_flatten)

    locate = subparsers.add_parser(
        "locate", help="trace a flattened card back to its source, or a source line forward"
    )
    locate.add_argument("path")
    locate.add_argument(
        "card",
        nargs="?",
        help="flattened card name, or its output index; tried as a name first",
    )
    locate.add_argument("--source", help="instead report every card one FILE:LINE produced")
    locate.add_argument(
        "--under", help="instead report every card produced beneath an INSTANCE/PATH"
    )
    locate.add_argument("--include-root", action="append", default=[])
    locate.add_argument("--json", action="store_true")
    locate.add_argument("-o", "--output")
    locate.add_argument("--force", action="store_true")
    locate.set_defaults(handler=_command_locate)

    formatter = subparsers.add_parser("format", help="render a canonical portable-SPICE deck")
    formatter.add_argument("path")
    formatter.add_argument("--check", action="store_true")
    formatter.add_argument("-o", "--output")
    formatter.add_argument("--force", action="store_true")
    formatter.set_defaults(handler=_command_format)

    inventory_command = subparsers.add_parser("inventory", help="summarize circuit contents")
    inventory_command.add_argument("path")
    inventory_command.add_argument("--include-root", action="append", default=[])
    inventory_command.add_argument(
        "--interop", action="store_true", help="emit the versioned cross-tool summary"
    )
    inventory_command.set_defaults(handler=_command_inventory)
    fuzz = subparsers.add_parser("fuzz-smoke", help="run bounded deterministic parser mutations")
    fuzz.add_argument("path")
    fuzz.add_argument("--cases", type=int, default=128)
    fuzz.add_argument("--seed", type=int, default=0)
    fuzz.set_defaults(handler=_command_fuzz)

    export_ir = subparsers.add_parser(
        "export-ir", help="analyze a project and emit versioned circuit IR"
    )
    export_ir.add_argument("path")
    export_ir.add_argument("-o", "--output")
    export_ir.add_argument("--force", action="store_true")
    export_ir.add_argument("--include-root", action="append", default=[])
    export_ir.add_argument("--compact", action="store_true")
    export_ir.add_argument("--require-lossless", action="store_true")
    export_ir.set_defaults(handler=_command_export_ir)

    check_ir = subparsers.add_parser(
        "check-ir", help="strictly validate circuit IR and report its identity"
    )
    check_ir.add_argument("path")
    check_ir.add_argument("--require-lossless", action="store_true")
    check_ir.set_defaults(handler=_command_check_ir)

    read_result = subparsers.add_parser(
        "read-result",
        help="convert simulator data to versioned result JSON (not proof of convergence)",
    )
    read_result.add_argument("path")
    read_result.add_argument("--format", required=True, choices=("raw", "xyce-csv"))
    read_result.add_argument("--byte-order", choices=("little", "big"))
    read_result.add_argument("--analysis", help="caller-declared analysis for Xyce CSV only")
    read_result.add_argument("-o", "--output")
    read_result.add_argument("--force", action="store_true")
    _add_result_limits(read_result)
    read_result.set_defaults(handler=_command_read_result)

    check_result = subparsers.add_parser(
        "check-result", help="validate result JSON and summarize its declared identity and shape"
    )
    check_result.add_argument("path")
    _add_result_limits(check_result)
    check_result.set_defaults(handler=_command_check_result)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, UnicodeError, ValueError) as error:
        sys.stderr.write(f"spice-trellis: {error}\n")
        return 2


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
