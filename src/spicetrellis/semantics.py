"""Include loading, symbol collection, and semantic validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields
from pathlib import Path

from spicetrellis.expressions import expression_names
from spicetrellis.model import (
    Analysis,
    Assignment,
    Blank,
    CircuitInventory,
    Comment,
    Diagnostic,
    Element,
    End,
    Global,
    Include,
    Model,
    Param,
    SemanticDeck,
    SourceSpan,
    Statement,
    Subcircuit,
    SubcktEnd,
    SubcktStart,
    SyntaxDeck,
    sorted_diagnostics,
)
from spicetrellis.parser import parse_text


@dataclass(frozen=True, slots=True)
class AnalysisLimits:
    """Resource limits applied while loading and expanding a SPICE project."""

    max_file_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 10 * 1024 * 1024
    max_files: int = 256
    max_include_depth: int = 64
    max_expanded_statements: int = 250_000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field.name} must be a positive integer")


DEFAULT_ANALYSIS_LIMITS = AnalysisLimits()


def _file_span(path: Path) -> SourceSpan:
    return SourceSpan(str(path), 1, 1, 1, 1)


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


class _ProjectLoader:
    def __init__(
        self, entry: Path, include_roots: tuple[Path, ...], limits: AnalysisLimits
    ) -> None:
        self.entry = entry
        self.roots = include_roots
        self.limits = limits
        self.files: dict[Path, SyntaxDeck] = {}
        self.source_bytes: dict[Path, bytes] = {}
        self.dependencies: set[Path] = set()
        self.attempted: set[Path] = set()
        self.total_bytes = 0
        self.expanded_statements = 0
        self.expansion_limit_reported = False
        self.diagnostics: list[Diagnostic] = []

    def _load(self, path: Path) -> SyntaxDeck | None:
        if path in self.files:
            return self.files[path]
        if path in self.attempted:
            return None
        if len(self.attempted) >= self.limits.max_files:
            self.diagnostics.append(
                Diagnostic(
                    "ST2007",
                    "error",
                    f"project exceeds the {self.limits.max_files}-file limit",
                    _file_span(path),
                )
            )
            return None
        self.attempted.add(path)
        if not _inside(path, self.roots):
            self.diagnostics.append(
                Diagnostic(
                    "ST2001",
                    "error",
                    f"input escapes the allowed include roots: {path}",
                    _file_span(path),
                )
            )
            return None
        try:
            remaining = self.limits.max_total_bytes - self.total_bytes
            read_limit = min(self.limits.max_file_bytes, max(remaining, 0))
            with path.open("rb") as source:
                content = source.read(read_limit + 1)
            if len(content) > self.limits.max_file_bytes:
                self.diagnostics.append(
                    Diagnostic(
                        "ST2005",
                        "error",
                        f"SPICE file exceeds the {self.limits.max_file_bytes}-byte limit",
                        _file_span(path),
                    )
                )
                return None
            if len(content) > remaining:
                self.diagnostics.append(
                    Diagnostic(
                        "ST2006",
                        "error",
                        f"project exceeds the {self.limits.max_total_bytes}-byte limit",
                        _file_span(path),
                    )
                )
                return None
            text = content.decode("utf-8-sig")
        except (OSError, UnicodeError) as error:
            self.diagnostics.append(
                Diagnostic("ST2002", "error", f"cannot read SPICE file: {error}", _file_span(path))
            )
            return None
        try:
            deck = parse_text(text, str(path))
        except RecursionError:
            self.diagnostics.append(
                Diagnostic(
                    "ST2009",
                    "error",
                    "SPICE syntax exceeds the parser nesting limit",
                    _file_span(path),
                )
            )
            return None
        self.total_bytes += len(content)
        self.files[path] = deck
        self.source_bytes[path] = content
        self.dependencies.add(path)
        self.diagnostics.extend(deck.diagnostics)
        return deck

    def expand(self, path: Path, stack: tuple[Path, ...] = ()) -> list[Statement]:
        if path in stack:
            cycle = " -> ".join(item.name for item in (*stack, path))
            self.diagnostics.append(
                Diagnostic("ST2003", "error", f"include cycle detected: {cycle}", _file_span(path))
            )
            return []
        if len(stack) >= self.limits.max_include_depth:
            self.diagnostics.append(
                Diagnostic(
                    "ST2008",
                    "error",
                    f"include nesting exceeds the {self.limits.max_include_depth}-level limit",
                    _file_span(path),
                )
            )
            return []
        deck = self._load(path)
        if deck is None:
            return []
        expanded: list[Statement] = []
        for statement in deck.statements:
            if not isinstance(statement, Include):
                if self.expanded_statements >= self.limits.max_expanded_statements:
                    if not self.expansion_limit_reported:
                        self.diagnostics.append(
                            Diagnostic(
                                "ST2010",
                                "error",
                                "expanded project exceeds the "
                                f"{self.limits.max_expanded_statements}-statement limit",
                                statement.meta.span,
                            )
                        )
                        self.expansion_limit_reported = True
                    continue
                self.expanded_statements += 1
                expanded.append(statement)
                continue
            candidate = Path(statement.target)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (path.parent / candidate).resolve()
            )
            if not _inside(resolved, self.roots):
                self.diagnostics.append(
                    Diagnostic(
                        "ST2004",
                        "error",
                        f"include path escapes the allowed roots: {statement.target}",
                        statement.meta.span,
                    )
                )
                continue
            expanded.extend(self.expand(resolved, (*stack, path)))
        return expanded


def _strong_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            result.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return result


def _validate_parameters(
    assignments: list[Assignment],
    spans: list[SourceSpan],
    external: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    definitions: dict[str, tuple[Assignment, SourceSpan]] = {}
    for assignment, span in zip(assignments, spans, strict=True):
        key = assignment.name.casefold()
        if key in definitions:
            diagnostics.append(
                Diagnostic(
                    "ST2101",
                    "error",
                    f"duplicate parameter {assignment.name!r}",
                    span,
                    (definitions[key][1],),
                )
            )
        else:
            definitions[key] = (assignment, span)
    local = set(definitions)
    graph: dict[str, set[str]] = {}
    for name, (assignment, span) in definitions.items():
        references = set(expression_names(assignment.expression))
        graph[name] = references & local
        for missing in sorted(references - local - external):
            diagnostics.append(
                Diagnostic(
                    "ST2102",
                    "error",
                    f"parameter {assignment.name!r} references undefined parameter {missing!r}",
                    span,
                )
            )
    for component in _strong_components(graph):
        cyclic = len(component) > 1 or component[0] in graph.get(component[0], set())
        if cyclic:
            diagnostics.append(
                Diagnostic(
                    "ST2103",
                    "error",
                    "parameter cycle: " + " -> ".join((*component, component[0])),
                    definitions[component[0]][1],
                    tuple(definitions[name][1] for name in component[1:]),
                )
            )


def _build_semantic(
    entry: Path,
    files: tuple[SyntaxDeck, ...],
    expanded: list[Statement],
    dependencies: tuple[Path, ...],
    diagnostics: list[Diagnostic],
) -> SemanticDeck:
    top: list[Statement] = []
    subcircuits: list[Subcircuit] = []
    current_start: SubcktStart | None = None
    current_body: list[Statement] = []
    ended = False

    for statement in expanded:
        if ended:
            if not isinstance(statement, (Blank, Comment)):
                diagnostics.append(
                    Diagnostic(
                        "ST2213",
                        "error",
                        "statement after the terminal .end is ignored",
                        statement.meta.span,
                    )
                )
            continue
        if isinstance(statement, End):
            if current_start is not None:
                diagnostics.append(
                    Diagnostic(
                        "ST2213",
                        "error",
                        f"terminal .end appears before .ends for {current_start.name!r}",
                        statement.meta.span,
                        (current_start.meta.span,),
                    )
                )
                current_body.append(statement)
            else:
                top.append(statement)
                ended = True
            continue
        if isinstance(statement, SubcktStart):
            if current_start is not None:
                diagnostics.append(
                    Diagnostic(
                        "ST2201",
                        "error",
                        "nested .subckt definitions are not supported",
                        statement.meta.span,
                        (current_start.meta.span,),
                    )
                )
                current_body.append(statement)
            else:
                current_start = statement
                current_body = []
            continue
        if isinstance(statement, SubcktEnd):
            if current_start is None:
                diagnostics.append(
                    Diagnostic("ST2202", "error", "unmatched .ends", statement.meta.span)
                )
                continue
            if statement.name and statement.name.casefold() != current_start.name.casefold():
                diagnostics.append(
                    Diagnostic(
                        "ST2203",
                        "error",
                        f".ends name {statement.name!r} does not match {current_start.name!r}",
                        statement.meta.span,
                        (current_start.meta.span,),
                    )
                )
            span = SourceSpan(
                current_start.meta.span.filename,
                current_start.meta.span.start_line,
                current_start.meta.span.start_col,
                statement.meta.span.end_line,
                statement.meta.span.end_col,
            )
            subcircuits.append(
                Subcircuit(
                    current_start.name,
                    current_start.pins,
                    current_start.defaults,
                    tuple(current_body),
                    span,
                )
            )
            current_start = None
            current_body = []
            continue
        if current_start is None:
            top.append(statement)
        else:
            current_body.append(statement)

    if current_start is not None:
        diagnostics.append(
            Diagnostic(
                "ST2204",
                "error",
                f"subcircuit {current_start.name!r} is missing .ends",
                current_start.meta.span,
            )
        )

    unique_subcircuits: list[Subcircuit] = []
    subckt_map: dict[str, Subcircuit] = {}
    for subckt in subcircuits:
        seen_pins: set[str] = set()
        for pin in subckt.pins:
            key = pin.casefold()
            if key in seen_pins:
                diagnostics.append(
                    Diagnostic(
                        "ST2212",
                        "error",
                        f"subcircuit {subckt.name!r} repeats formal pin {pin!r}",
                        subckt.span,
                    )
                )
            seen_pins.add(key)
        key = subckt.name.casefold()
        if key in subckt_map:
            diagnostics.append(
                Diagnostic(
                    "ST2205",
                    "error",
                    f"duplicate subcircuit {subckt.name!r}",
                    subckt.span,
                    (subckt_map[key].span,),
                )
            )
        else:
            subckt_map[key] = subckt
            unique_subcircuits.append(subckt)

    global_assignments: list[Assignment] = []
    global_spans: list[SourceSpan] = []
    global_nodes: list[str] = []
    for statement in top:
        if isinstance(statement, Param):
            global_assignments.extend(statement.assignments)
            global_spans.extend([statement.meta.span] * len(statement.assignments))
        elif isinstance(statement, Global):
            global_nodes.extend(statement.nodes)
    _validate_parameters(global_assignments, global_spans, set(), diagnostics)
    global_names = {assignment.name.casefold() for assignment in global_assignments}

    scopes: list[tuple[str, tuple[Statement, ...]]] = [("<top>", tuple(top))]
    scopes.extend((subckt.name, subckt.body) for subckt in unique_subcircuits)
    for scope_name, statements in scopes:
        seen_elements: dict[str, Element] = {}
        for statement in statements:
            if not isinstance(statement, Element):
                continue
            key = statement.name.casefold()
            if key in seen_elements:
                diagnostics.append(
                    Diagnostic(
                        "ST2206",
                        "error",
                        f"duplicate element {statement.name!r} in scope {scope_name}",
                        statement.meta.span,
                        (seen_elements[key].meta.span,),
                    )
                )
            else:
                seen_elements[key] = statement
            if statement.family == "X":
                target = subckt_map.get((statement.model or "").casefold())
                if target is None:
                    diagnostics.append(
                        Diagnostic(
                            "ST2207",
                            "error",
                            f"unknown subcircuit {statement.model!r}",
                            statement.meta.span,
                        )
                    )
                elif len(statement.nodes) != len(target.pins):
                    diagnostics.append(
                        Diagnostic(
                            "ST2208",
                            "error",
                            f"instance {statement.name!r} supplies {len(statement.nodes)} nodes "
                            f"but {target.name!r} requires {len(target.pins)}",
                            statement.meta.span,
                            (target.span,),
                        )
                    )
                else:
                    allowed = {item.name.casefold() for item in target.defaults}
                    seen_overrides: dict[str, Assignment] = {}
                    for override in statement.parameters:
                        override_key = override.name.casefold()
                        if override_key not in allowed:
                            diagnostics.append(
                                Diagnostic(
                                    "ST2210",
                                    "error",
                                    f"instance {statement.name!r} overrides unknown parameter "
                                    f"{override.name!r}",
                                    statement.meta.span,
                                    (target.span,),
                                )
                            )
                        if override_key in seen_overrides:
                            diagnostics.append(
                                Diagnostic(
                                    "ST2211",
                                    "error",
                                    f"instance {statement.name!r} repeats override "
                                    f"{override.name!r}",
                                    statement.meta.span,
                                )
                            )
                        seen_overrides[override_key] = override

    call_graph: dict[str, set[str]] = {key: set() for key in subckt_map}
    call_spans: dict[tuple[str, str], SourceSpan] = {}
    for subckt in unique_subcircuits:
        caller = subckt.name.casefold()
        for statement in subckt.body:
            if isinstance(statement, Element) and statement.family == "X" and statement.model:
                callee = statement.model.casefold()
                if callee in subckt_map:
                    call_graph[caller].add(callee)
                    call_spans[(caller, callee)] = statement.meta.span
    for component in _strong_components(call_graph):
        cyclic = len(component) > 1 or component[0] in call_graph.get(component[0], set())
        if cyclic:
            first = component[0]
            next_name = component[1] if len(component) > 1 else first
            diagnostics.append(
                Diagnostic(
                    "ST2209",
                    "error",
                    "recursive subcircuit call graph: " + " -> ".join((*component, first)),
                    call_spans.get((first, next_name), subckt_map[first].span),
                )
            )

    for subckt in unique_subcircuits:
        local_assignments = list(subckt.defaults)
        local_spans = [subckt.span] * len(subckt.defaults)
        for statement in subckt.body:
            if isinstance(statement, Param):
                local_assignments.extend(statement.assignments)
                local_spans.extend([statement.meta.span] * len(statement.assignments))
        _validate_parameters(local_assignments, local_spans, global_names, diagnostics)

    return SemanticDeck(
        entry,
        files,
        tuple(top),
        tuple(unique_subcircuits),
        tuple(global_assignments),
        tuple(dict.fromkeys(node.casefold() for node in global_nodes)),
        dependencies,
    )


def analyze_file(
    path: str | Path,
    *,
    include_roots: tuple[str | Path, ...] = (),
    limits: AnalysisLimits = DEFAULT_ANALYSIS_LIMITS,
) -> Analysis:
    entry = Path(path).resolve()
    roots = tuple(Path(root).resolve() for root in include_roots) or (entry.parent,)
    if not _inside(entry, roots):
        roots = (entry.parent, *roots)
    loader = _ProjectLoader(entry, roots, limits)
    expanded = loader.expand(entry)
    dependencies = tuple(sorted(loader.dependencies, key=lambda item: str(item).casefold()))
    files = tuple(loader.files[path] for path in dependencies)
    if entry not in loader.files:
        snapshots = tuple((path, loader.source_bytes[path]) for path in dependencies)
        return Analysis(None, sorted_diagnostics(loader.diagnostics), dependencies, snapshots)
    deck = _build_semantic(entry, files, expanded, dependencies, loader.diagnostics)
    snapshots = tuple((path, loader.source_bytes[path]) for path in dependencies)
    return Analysis(deck, sorted_diagnostics(loader.diagnostics), dependencies, snapshots)


def inventory(analysis: Analysis) -> CircuitInventory:
    if analysis.deck is None:
        return CircuitInventory(0, 0, (), (), (), ())
    deck = analysis.deck
    families: Counter[str] = Counter()
    models: set[str] = set()
    includes = 0
    for syntax in deck.files:
        includes += sum(isinstance(statement, Include) for statement in syntax.statements)
        for statement in syntax.statements:
            if isinstance(statement, Element):
                families[statement.family] += 1
            elif isinstance(statement, Model):
                models.add(statement.name)
    parameters = {assignment.name for assignment in deck.global_parameters}
    for subckt in deck.subcircuits:
        parameters.update(assignment.name for assignment in subckt.defaults)
        for statement in subckt.body:
            if isinstance(statement, Param):
                parameters.update(assignment.name for assignment in statement.assignments)
    subcircuits = tuple(
        {
            "name": subckt.name,
            "pins": list(subckt.pins),
            "elements": sum(isinstance(statement, Element) for statement in subckt.body),
        }
        for subckt in sorted(deck.subcircuits, key=lambda item: item.name.casefold())
    )
    return CircuitInventory(
        len(deck.files),
        includes,
        subcircuits,
        tuple(sorted(families.items())),
        tuple(sorted(parameters, key=str.casefold)),
        tuple(sorted(models, key=str.casefold)),
    )
