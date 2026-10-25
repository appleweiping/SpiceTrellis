"""Hierarchical subcircuit expansion into a flat deck ngspice can still run.

Names follow ngspice's own convention for expanded subcircuits: device
``R1`` inside instance ``X1`` becomes ``R.X1.R1`` (the device letter stays
first, so ngspice still knows what the card is) and node ``mid`` becomes
``X1.mid``.  A ``print v(x1.mid)`` written for the hierarchical deck therefore
still works on the flat one.

Flattening has to do more than copy cards with longer names:

* parameters are folded to numbers, but anything that depends on the running
  simulation (``v()``, ``i()``, ``temper``, ``agauss()``) stays an expression,
  with node and device names inside it renamed;
* a ``.model`` declared inside a subcircuit is copied out once per distinct
  set of parameter values, keeping the ``name.N`` bin suffixes ngspice uses to
  pick a bin, because PDK models usually read the instance's ``l``, ``w`` and
  ``mult``;
* ``m=`` on an instance multiplies every device inside it;
* names an F/H/W/K card refers to are renamed along with the devices.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from decimal import Decimal

from spicetrellis.expressions import (
    Binary,
    Expr,
    ExpressionError,
    FunctionDef,
    Name,
    Number,
    expression_names,
    fold_expression,
    format_decimal,
    spice_expression,
)
from spicetrellis.model import (
    Analysis,
    Assignment,
    Blank,
    Comment,
    Control,
    Diagnostic,
    Directive,
    ElaboratedDeck,
    Element,
    End,
    Func,
    Global,
    Model,
    Opaque,
    Option,
    Param,
    Provenance,
    SemanticDeck,
    SourceSpan,
    Statement,
    Subcircuit,
    Title,
    sorted_diagnostics,
)
from spicetrellis.parser import CardParseError, model_parameters, tail_expressions
from spicetrellis.semantics import model_names, subcircuit_parameter_names

Environment = dict[str, Expr]

# Families that accept an m= multiplier in ngspice.
_MULTIPLIABLE = frozenset("RCLDQJZMBGFIX")
_ONE = Number(Decimal(1), "1")


@dataclass(frozen=True, slots=True)
class ElaborationLimits:
    """Upper bounds for hierarchical flattening."""

    max_depth: int = 64
    max_output_statements: int = 500_000
    max_provenance_records: int = 500_000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field.name} must be a positive integer")


DEFAULT_ELABORATION_LIMITS = ElaborationLimits()


def _functions(statements: tuple[Statement, ...] | list[Statement]) -> dict[str, FunctionDef]:
    return {
        item.name.casefold(): FunctionDef(item.name, item.parameters, item.body)
        for item in statements
        if isinstance(item, Func)
    }


def _resolve_assignments(
    assignments: tuple[Assignment, ...] | list[Assignment],
    base: Mapping[str, Expr],
    functions: Mapping[str, FunctionDef],
    diagnostics: list[Diagnostic],
    span: SourceSpan,
) -> Environment:
    """Evaluate a parameter list in dependency order; the last definition of a name wins."""

    environment: Environment = dict(base)
    pending = {assignment.name.casefold(): assignment for assignment in assignments}
    missing: dict[str, set[str]] = {}
    dependents: defaultdict[str, set[str]] = defaultdict(set)
    for name, assignment in pending.items():
        needed = set(expression_names(assignment.expression)) & pending.keys()
        needed.discard(name)
        missing[name] = needed
        for dependency in needed:
            dependents[dependency].add(name)
    ready = deque(sorted(name for name in pending if not missing[name]))
    while ready:
        name = ready.popleft()
        assignment = pending.pop(name)
        scope = environment
        if name in expression_names(assignment.expression) and name in base:
            scope = {**environment, name: base[name]}
        try:
            environment[name] = fold_expression(assignment.expression, scope, functions=functions)
        except ExpressionError as error:
            diagnostics.append(
                Diagnostic(
                    "ST3001",
                    "error",
                    f"cannot evaluate parameter {assignment.name!r}: {error}",
                    span,
                )
            )
            continue
        for dependent in sorted(dependents[name]):
            if dependent not in pending:
                continue
            missing[dependent].discard(name)
            if not missing[dependent]:
                ready.append(dependent)
    for assignment in pending.values():
        diagnostics.append(
            Diagnostic(
                "ST3001",
                "error",
                f"cannot evaluate parameter {assignment.name!r}: it depends on itself",
                span,
            )
        )
    return environment


def _number_or_none(expression: Expr) -> Decimal | None:
    return expression.value if isinstance(expression, Number) else None


def _multiply(left: Expr, right: Expr) -> Expr:
    left_value, right_value = _number_or_none(left), _number_or_none(right)
    if left_value is not None and right_value is not None:
        product = left_value * right_value
        return Number(product, format_decimal(product))
    if left_value == 1:
        return right
    if right_value == 1:
        return left
    return Binary("*", left, right)


@dataclass(slots=True)
class _Scope:
    """Everything needed to emit the cards of one subcircuit instance."""

    prefix: str
    nodes: dict[str, str]
    environment: Environment
    functions: dict[str, FunctionDef]
    models: dict[str, list[Model]]
    model_owner: str
    multiplier: Expr
    chain: tuple[SourceSpan, ...]


class _Elaborator:
    def __init__(self, deck: SemanticDeck, limits: ElaborationLimits) -> None:
        self.deck = deck
        self.limits = limits
        self.subcircuits = deck.subcircuit_map()
        self.global_nodes = {"0", "gnd", *deck.global_nodes}
        self.global_models = model_names(deck.top)
        self.global_functions = _functions(deck.top)
        self.diagnostics: list[Diagnostic] = []
        self.output: list[Statement] = []
        self.provenance: list[Provenance] = []
        self.output_names: dict[str, SourceSpan] = {}
        self.hoisted: dict[tuple[str, str, tuple[str, ...]], str] = {}
        self.model_variants: dict[str, int] = {}
        self.stopped = False
        fallback_span = (
            deck.top[0].meta.span
            if deck.top
            else deck.subcircuits[0].span
            if deck.subcircuits
            else SourceSpan(str(deck.entry), 1, 1, 1, 1)
        )
        self.environment = _resolve_assignments(
            deck.global_parameters, {}, self.global_functions, self.diagnostics, fallback_span
        )

    # -- output -----------------------------------------------------------------

    def _stop(self, code: str, message: str, span: SourceSpan) -> None:
        if not self.stopped:
            self.diagnostics.append(Diagnostic(code, "error", message, span))
        self.stopped = True

    def _room(self, span: SourceSpan) -> bool:
        if len(self.output) >= self.limits.max_output_statements:
            self._stop(
                "ST3009",
                f"flattened output exceeds the {self.limits.max_output_statements}-statement limit",
                span,
            )
            return False
        return True

    def _append_passthrough(self, statement: Statement) -> bool:
        if not self._room(statement.meta.span):
            return False
        self.output.append(statement)
        return True

    # -- names ------------------------------------------------------------------

    def _node(self, node: str, mapping: dict[str, str], prefix: str) -> str:
        key = node.casefold()
        if key in self.global_nodes:
            return node
        if key in mapping:
            return mapping[key]
        if not prefix:
            mapping[key] = node
            return node
        generated = f"{prefix}.{node}"
        mapping[key] = generated
        return generated

    @staticmethod
    def _device(name: str, prefix: str) -> str:
        return f"{name[0]}.{prefix}.{name}" if prefix else name

    def _model_name(self, base: str) -> str:
        """A short, unique name for one copied-out variant of a local model."""

        while True:
            count = self.model_variants.get(base.casefold(), 0) + 1
            self.model_variants[base.casefold()] = count
            candidate = f"{base}_{count}"
            if candidate.casefold() not in self.global_models:
                return candidate

    # -- expressions ------------------------------------------------------------

    def _fold(self, expression: Expr, scope: _Scope) -> Expr:
        return fold_expression(
            expression,
            scope.environment,
            functions=scope.functions,
            node=lambda name: self._node(name, scope.nodes, scope.prefix),
            device=lambda name: self._device(name, scope.prefix),
        )

    def _fold_assignments(
        self, assignments: tuple[Assignment, ...], scope: _Scope, span: SourceSpan
    ) -> tuple[Assignment, ...]:
        result: list[Assignment] = []
        for assignment in assignments:
            try:
                folded = self._fold(assignment.expression, scope)
            except ExpressionError as error:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3002", "error", f"cannot evaluate {assignment.name!r}: {error}", span
                    )
                )
                result.append(assignment)
                continue
            result.append(Assignment(assignment.name, folded, spice_expression(folded)))
        return tuple(result)

    def _substitute_tail(self, tail: str, scope: _Scope, span: SourceSpan) -> str:
        try:
            found = tail_expressions(tail)
        except CardParseError as error:
            self.diagnostics.append(Diagnostic("ST3003", "error", str(error), span))
            return tail
        pieces: list[str] = []
        position = 0
        for match, expression in found:
            pieces.append(tail[position : match.start()])
            try:
                pieces.append(spice_expression(self._fold(expression, scope)))
            except ExpressionError as error:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3003", "error", f"cannot substitute source expression: {error}", span
                    )
                )
                pieces.append(match.group(0))
            position = match.end()
        pieces.append(tail[position:])
        return "".join(pieces)

    # -- models -----------------------------------------------------------------

    def _model_reference(self, element: Element, scope: _Scope) -> str | None:
        """Rename a reference to a subcircuit-local model, copying the model out."""

        model = element.model
        if model is None:
            return None
        key = model.casefold()
        base, dot, suffix = key.rpartition(".")
        if key in scope.models:
            group_key, bin_suffix = key, ""
        elif dot and suffix.isdigit() and base in scope.models:
            group_key, bin_suffix = base, "." + suffix
        else:
            return model
        generated = self._hoist(group_key, scope, element.meta.span)
        return generated + bin_suffix if generated is not None else model

    def _hoist(self, group_key: str, scope: _Scope, span: SourceSpan) -> str | None:
        group = scope.models[group_key]
        rendered: list[tuple[Model, str]] = []
        for model in group:
            try:
                parameters = model_parameters(model.tail)
            except CardParseError:
                rendered.append((model, self._substitute_tail(model.tail, scope, span)))
                continue
            folded = self._fold_assignments(parameters, scope, model.meta.span)
            rendered.append(
                (
                    model,
                    " ".join(f"{item.name}={spice_expression(item.expression)}" for item in folded),
                )
            )
        content = (scope.model_owner, group_key, tuple(text for _model, text in rendered))
        existing = self.hoisted.get(content)
        if existing is not None:
            return existing
        base_name = group[0].name
        if len(group) > 1 or group_key != base_name.casefold():
            base_name = base_name.rpartition(".")[0]
        generated = self._model_name(base_name)
        self.hoisted[content] = generated
        for model, text in rendered:
            suffix = model.name[len(base_name) :] if len(model.name) > len(base_name) else ""
            if not self._room(span):
                return None
            self.output.append(replace(model, name=generated + suffix, tail=text))
        return generated

    # -- devices ----------------------------------------------------------------

    def _append_primitive(self, element: Element, scope: _Scope) -> None:
        if self.stopped or not self._room(element.meta.span):
            return
        if len(self.provenance) >= self.limits.max_provenance_records:
            self._stop(
                "ST3010",
                "flattened provenance exceeds the "
                f"{self.limits.max_provenance_records}-record limit",
                element.meta.span,
            )
            return
        span = element.meta.span
        generated_name = self._device(element.name, scope.prefix)
        name_key = generated_name.casefold()
        if name_key in self.output_names:
            self.diagnostics.append(
                Diagnostic(
                    "ST3008",
                    "error",
                    f"flattened element name collision for {generated_name!r}",
                    span,
                    (self.output_names[name_key],),
                )
            )
            return
        value = element.value
        model = element.model
        if (
            element.family in {"R", "C", "L"}
            and value is None
            and model is not None
            and model.casefold() not in scope.models
            and model.casefold() not in self.global_models
            and model.casefold() in scope.environment
        ):
            # ngspice falls back to a parameter when no model has this name.
            value, model = Name(model), None
        if value is not None:
            try:
                value = self._fold(value, scope)
            except ExpressionError as error:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3004",
                        "error",
                        f"cannot evaluate value of {element.name!r}: {error}",
                        span,
                    )
                )
        parameters = self._fold_assignments(element.parameters, scope, span)
        if _number_or_none(scope.multiplier) != 1:
            if element.family not in _MULTIPLIABLE:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3013",
                        "error",
                        f"cannot apply the instance multiplier m= to {element.name!r}",
                        span,
                    )
                )
            existing = next((item for item in parameters if item.name.casefold() == "m"), None)
            factor = (
                _multiply(existing.expression, scope.multiplier) if existing else scope.multiplier
            )
            parameters = (
                *(item for item in parameters if item.name.casefold() != "m"),
                Assignment("m", factor, spice_expression(factor)),
            )
        if element.family != "X":
            model = self._model_reference(replace(element, model=model), scope)
        flattened = replace(
            element,
            name=generated_name,
            nodes=tuple(self._node(node, scope.nodes, scope.prefix) for node in element.nodes),
            controls=tuple(self._device(item, scope.prefix) for item in element.controls),
            value=value,
            model=model,
            parameters=parameters,
            tail=self._substitute_tail(element.tail, scope, span),
        )
        self.output.append(flattened)
        self.output_names[name_key] = span
        self.provenance.append(
            Provenance(len(self.output) - 1, generated_name, span, tuple(scope.chain))
        )

    def _overrides(
        self, instance: Element, parent: _Scope, target: Subcircuit
    ) -> tuple[Environment, Expr]:
        """Evaluate an instance's parameters; they may read each other, as xschem writes them."""

        declared = subcircuit_parameter_names(target)
        pending = {item.name.casefold(): item for item in instance.parameters}
        resolved: Environment = {}
        progress = True
        while pending and progress:
            progress = False
            for key in sorted(pending):
                assignment = pending[key]
                needs = (set(expression_names(assignment.expression)) & pending.keys()) - {key}
                if needs:
                    continue
                scope_environment = {**parent.environment, **resolved}
                scope_environment.pop(key, None)
                if key in parent.environment:
                    scope_environment[key] = parent.environment[key]
                try:
                    resolved[key] = fold_expression(
                        assignment.expression,
                        scope_environment,
                        functions=parent.functions,
                        node=lambda name: self._node(name, parent.nodes, parent.prefix),
                        device=lambda name: self._device(name, parent.prefix),
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
                del pending[key]
                progress = True
        for assignment in pending.values():
            self.diagnostics.append(
                Diagnostic(
                    "ST3007",
                    "error",
                    f"cannot evaluate override {assignment.name!r}: overrides depend on each other",
                    instance.meta.span,
                )
            )
        multiplier = parent.multiplier
        if "m" in resolved and "m" not in declared:
            multiplier = _multiply(parent.multiplier, resolved.pop("m"))
        return resolved, multiplier

    def _expand_instance(self, instance: Element, parent: _Scope, active: tuple[str, ...]) -> None:
        if self.stopped:
            return
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
        if len(parent.chain) >= self.limits.max_depth:
            self._stop(
                "ST3011",
                f"hierarchy exceeds the {self.limits.max_depth}-level elaboration limit",
                instance.meta.span,
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
        prefix = f"{parent.prefix}.{instance.name}" if parent.prefix else instance.name
        actual = [self._node(node, parent.nodes, parent.prefix) for node in instance.nodes]
        mapping = {pin.casefold(): node for pin, node in zip(target.pins, actual, strict=True)}
        overrides, multiplier = self._overrides(instance, parent, target)
        functions = {**self.global_functions, **_functions(target.body)}
        defaults = [item for item in target.defaults if item.name.casefold() not in overrides]
        body_parameters = [
            assignment
            for statement in target.body
            if isinstance(statement, Param)
            for assignment in statement.assignments
            if assignment.name.casefold() not in overrides
        ]
        environment = _resolve_assignments(
            (*defaults, *body_parameters),
            {**self.environment, **overrides},
            functions,
            self.diagnostics,
            target.span,
        )
        local_models: dict[str, list[Model]] = {}
        for statement in target.body:
            if isinstance(statement, Model):
                key = statement.name.casefold()
                base, dot, suffix = key.rpartition(".")
                local_models.setdefault(base if dot and suffix.isdigit() else key, []).append(
                    statement
                )
        scope = _Scope(
            prefix,
            mapping,
            environment,
            functions,
            local_models,
            target.name.casefold(),
            multiplier,
            (*parent.chain, instance.meta.span),
        )
        for statement in target.body:
            if self.stopped:
                break
            if isinstance(statement, (Param, Func, Model, Comment, Blank, Global)):
                continue
            if isinstance(statement, Element) and statement.family == "X":
                self._expand_instance(statement, scope, (*active, target_name))
            elif isinstance(statement, Element):
                self._append_primitive(statement, scope)
            elif isinstance(statement, Option):
                self._append_passthrough(statement)
            else:
                self.diagnostics.append(
                    Diagnostic(
                        "ST3012",
                        "error",
                        "cannot flatten this card inside a subcircuit: its node names would "
                        "not be renamed",
                        statement.meta.span,
                    )
                )

    def run(self) -> ElaboratedDeck:
        scope = _Scope(
            "",
            {},
            self.environment,
            self.global_functions,
            {},
            "",
            _ONE,
            (),
        )
        end_statement: End | None = None
        for statement in self.deck.top:
            if self.stopped:
                break
            if isinstance(statement, End):
                end_statement = statement
            elif isinstance(statement, Element) and statement.family == "X":
                self._expand_instance(statement, scope, ())
            elif isinstance(statement, Element):
                self._append_primitive(statement, scope)
            elif isinstance(
                statement,
                (
                    Title,
                    Param,
                    Func,
                    Model,
                    Global,
                    Option,
                    Directive,
                    Control,
                    Comment,
                    Blank,
                    Opaque,
                ),
            ):
                self._append_passthrough(statement)
        if end_statement is not None and not self.stopped:
            self._append_passthrough(end_statement)
        return ElaboratedDeck(
            tuple(self.output), tuple(self.provenance), sorted_diagnostics(self.diagnostics)
        )


def flatten(
    analysis: Analysis, *, limits: ElaborationLimits = DEFAULT_ELABORATION_LIMITS
) -> ElaboratedDeck:
    if analysis.deck is None:
        return ElaboratedDeck((), (), analysis.diagnostics)
    if analysis.has_errors:
        return ElaboratedDeck((), (), analysis.diagnostics)
    return _Elaborator(analysis.deck, limits).run()
