"""Deterministic hierarchical subcircuit expansion."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal

from spicetrellis.expressions import (
    ExpressionError,
    Number,
    evaluate_expression,
    format_decimal,
    parse_expression,
)
from spicetrellis.model import (
    Analysis,
    Assignment,
    Blank,
    Comment,
    Diagnostic,
    ElaboratedDeck,
    Element,
    End,
    Global,
    Model,
    Opaque,
    Param,
    Provenance,
    SemanticDeck,
    SourceSpan,
    Statement,
    sorted_diagnostics,
)

_BRACED_EXPRESSION = re.compile(r"\{([^{}]+)\}")


def _encode_segment(value: str) -> str:
    """Encode one name segment without ever producing the ``__`` path delimiter."""

    result: list[str] = []
    for byte in value.encode("utf-8"):
        character = chr(byte)
        if character.isascii() and (character.isalnum() or character in ".$:-"):
            result.append(character)
        elif character == "_":
            result.append("_u")
        else:
            result.append(f"_x{byte:02x}")
    return "".join(result) or "_e"


def _resolve_assignments(
    assignments: tuple[Assignment, ...] | list[Assignment],
    base: dict[str, Decimal],
    diagnostics: list[Diagnostic],
    span: SourceSpan,
) -> dict[str, Decimal]:
    environment = dict(base)
    pending = list(assignments)
    while pending:
        progress = False
        remaining: list[Assignment] = []
        for assignment in pending:
            try:
                environment[assignment.name.casefold()] = evaluate_expression(
                    assignment.expression, environment
                )
                progress = True
            except ExpressionError:
                remaining.append(assignment)
        if not progress:
            for assignment in remaining:
                diagnostics.append(
                    Diagnostic(
                        "ST3001",
                        "error",
                        f"cannot evaluate parameter {assignment.name!r} during elaboration",
                        span,
                    )
                )
            break
        pending = remaining
    return environment


def _evaluated_assignments(
    assignments: tuple[Assignment, ...],
    environment: dict[str, Decimal],
    diagnostics: list[Diagnostic],
    span: SourceSpan,
) -> tuple[Assignment, ...]:
    result: list[Assignment] = []
    for assignment in assignments:
        try:
            value = evaluate_expression(assignment.expression, environment)
            rendered = format_decimal(value)
            result.append(Assignment(assignment.name, Number(value, rendered), rendered))
        except ExpressionError as error:
            diagnostics.append(
                Diagnostic(
                    "ST3002",
                    "error",
                    f"cannot evaluate {assignment.name!r}: {error}",
                    span,
                )
            )
            result.append(assignment)
    return tuple(result)


def _substitute_tail(
    tail: str,
    environment: dict[str, Decimal],
    diagnostics: list[Diagnostic],
    span: SourceSpan,
) -> str:
    def replace_match(match: re.Match[str]) -> str:
        try:
            expression = parse_expression(match.group(1))
            return format_decimal(evaluate_expression(expression, environment))
        except ExpressionError as error:
            diagnostics.append(
                Diagnostic("ST3003", "error", f"cannot substitute source expression: {error}", span)
            )
            return match.group(0)

    return _BRACED_EXPRESSION.sub(replace_match, tail)


class _Elaborator:
    def __init__(self, deck: SemanticDeck) -> None:
        self.deck = deck
        self.subcircuits = deck.subcircuit_map()
        self.global_nodes = {"0", *deck.global_nodes}
        self.diagnostics: list[Diagnostic] = []
        self.output: list[Statement] = []
        self.provenance: list[Provenance] = []
        self.output_names: dict[str, SourceSpan] = {}
        fallback_span = (
            deck.top[0].meta.span
            if deck.top
            else deck.subcircuits[0].span
            if deck.subcircuits
            else SourceSpan(str(deck.entry), 1, 1, 1, 1)
        )
        self.environment = _resolve_assignments(
            deck.global_parameters, {}, self.diagnostics, fallback_span
        )

    def _node(self, node: str, mapping: dict[str, str], prefix: str) -> str:
        key = node.casefold()
        if key in self.global_nodes:
            return node
        if key in mapping:
            return mapping[key]
        if not prefix:
            mapping[key] = node
            return node
        generated = f"{prefix}__n__{_encode_segment(node)}"
        mapping[key] = generated
        return generated

    def _append_primitive(
        self,
        element: Element,
        *,
        prefix: str,
        node_mapping: dict[str, str],
        environment: dict[str, Decimal],
        chain: tuple[SourceSpan, ...],
    ) -> None:
        element_segment = _encode_segment(element.name)
        generated_name = f"{prefix}__{element_segment}" if prefix else element_segment
        name_key = generated_name.casefold()
        if name_key in self.output_names:
            self.diagnostics.append(
                Diagnostic(
                    "ST3008",
                    "error",
                    f"flattened element name collision for {generated_name!r}",
                    element.meta.span,
                    (self.output_names[name_key],),
                )
            )
            return
        nodes = tuple(self._node(node, node_mapping, prefix) for node in element.nodes)
        value = element.value
        if value is not None:
            try:
                numeric = evaluate_expression(value, environment)
                value = Number(numeric, format_decimal(numeric))
            except ExpressionError as error:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3004",
                        "error",
                        f"cannot evaluate value of {element.name!r}: {error}",
                        element.meta.span,
                    )
                )
        parameters = _evaluated_assignments(
            element.parameters, environment, self.diagnostics, element.meta.span
        )
        tail = _substitute_tail(element.tail, environment, self.diagnostics, element.meta.span)
        flattened = replace(
            element,
            name=generated_name,
            nodes=nodes,
            value=value,
            parameters=parameters,
            tail=tail,
        )
        self.output.append(flattened)
        self.output_names[name_key] = element.meta.span
        self.provenance.append(
            Provenance(
                len(self.output) - 1,
                generated_name,
                element.meta.span,
                tuple(chain),
            )
        )

    def _expand_instance(
        self,
        instance: Element,
        *,
        parent_mapping: dict[str, str],
        parent_environment: dict[str, Decimal],
        parent_prefix: str,
        chain: tuple[SourceSpan, ...],
        active: tuple[str, ...],
    ) -> None:
        target_name = (instance.model or "").casefold()
        target = self.subcircuits.get(target_name)
        if target is None:
            self.diagnostics.append(
                Diagnostic(
                    "ST3005",
                    "error",
                    f"cannot expand unknown subcircuit {instance.model!r}",
                    instance.meta.span,
                )
            )
            return
        if target_name in active:
            self.diagnostics.append(
                Diagnostic(
                    "ST3006",
                    "error",
                    "recursive expansion: " + " -> ".join((*active, target_name)),
                    instance.meta.span,
                )
            )
            return
        if len(instance.nodes) != len(target.pins):
            return
        segment = _encode_segment(instance.name)
        prefix = f"{parent_prefix}__{segment}" if parent_prefix else segment
        actual_nodes = [self._node(node, parent_mapping, parent_prefix) for node in instance.nodes]
        mapping = {
            pin.casefold(): actual for pin, actual in zip(target.pins, actual_nodes, strict=True)
        }
        override_values: list[Assignment] = []
        for assignment in instance.parameters:
            try:
                value = evaluate_expression(assignment.expression, parent_environment)
                rendered = format_decimal(value)
                override_values.append(
                    Assignment(assignment.name, Number(value, rendered), rendered)
                )
            except ExpressionError as error:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3007",
                        "error",
                        f"cannot evaluate override {assignment.name!r}: {error}",
                        instance.meta.span,
                    )
                )
        override_names = {assignment.name.casefold() for assignment in override_values}
        base_environment = dict(self.environment)
        for assignment in override_values:
            base_environment[assignment.name.casefold()] = evaluate_expression(
                assignment.expression, {}
            )
        remaining_defaults = tuple(
            assignment
            for assignment in target.defaults
            if assignment.name.casefold() not in override_names
        )
        environment = _resolve_assignments(
            (
                *remaining_defaults,
                *(
                    assignment
                    for statement in target.body
                    if isinstance(statement, Param)
                    for assignment in statement.assignments
                ),
            ),
            base_environment,
            self.diagnostics,
            target.span,
        )
        next_chain = (*chain, instance.meta.span)
        for statement in target.body:
            if isinstance(statement, Param):
                continue
            elif isinstance(statement, Element) and statement.family == "X":
                self._expand_instance(
                    statement,
                    parent_mapping=mapping,
                    parent_environment=environment,
                    parent_prefix=prefix,
                    chain=next_chain,
                    active=(*active, target_name),
                )
            elif isinstance(statement, Element):
                self._append_primitive(
                    statement,
                    prefix=prefix,
                    node_mapping=mapping,
                    environment=environment,
                    chain=next_chain,
                )
            elif isinstance(statement, (Comment, Blank, Opaque)):
                self.output.append(statement)

    def run(self) -> ElaboratedDeck:
        top_mapping: dict[str, str] = {}
        end_statement: End | None = None
        for statement in self.deck.top:
            if isinstance(statement, Param):
                continue
            if isinstance(statement, End):
                end_statement = statement
            elif isinstance(statement, Element) and statement.family == "X":
                self._expand_instance(
                    statement,
                    parent_mapping=top_mapping,
                    parent_environment=self.environment,
                    parent_prefix="",
                    chain=(),
                    active=(),
                )
            elif isinstance(statement, Element):
                self._append_primitive(
                    statement,
                    prefix="",
                    node_mapping=top_mapping,
                    environment=self.environment,
                    chain=(),
                )
            elif isinstance(statement, (Model, Global, Comment, Blank, Opaque)):
                self.output.append(statement)
        if end_statement is not None:
            self.output.append(end_statement)
        return ElaboratedDeck(
            tuple(self.output), tuple(self.provenance), sorted_diagnostics(self.diagnostics)
        )


def flatten(analysis: Analysis) -> ElaboratedDeck:
    if analysis.deck is None:
        return ElaboratedDeck((), (), analysis.diagnostics)
    if analysis.has_errors:
        return ElaboratedDeck((), (), analysis.diagnostics)
    return _Elaborator(analysis.deck).run()
