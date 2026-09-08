"""Versioned, language-neutral circuit interchange for the supported SPICE subset.

The syntax tree intentionally retains parser implementation details.  ``CircuitIR``
is the stable boundary for other tools: includes are resolved, hierarchy is explicit,
expressions are canonical strings, every instance has a deterministic identity, and
unsupported cards are retained as declared losses instead of disappearing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from spicetrellis._output import write_text_atomic
from spicetrellis.expressions import format_expression, parse_expression
from spicetrellis.model import (
    Analysis,
    Element,
    Model,
    Opaque,
    Param,
    SemanticDeck,
    SourceSpan,
    Statement,
    Subcircuit,
)

IR_SCHEMA = "org.spicetrellis.circuit-ir"
IR_VERSION = 1
MAX_IR_BYTES = 4 * 1024 * 1024
MAX_MODULES = 4_096
MAX_INSTANCES = 1_000_000
MAX_ITEMS = 100_000
MAX_STRING_CHARS = 1_000_000
MAX_SOURCE_COORDINATE = 2_147_483_647
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.$:-]*\Z")
_FAMILIES = frozenset({"R", "C", "L", "V", "I", "M", "X"})
_VALIDATED_IR_TOKEN = object()


class CircuitIRError(ValueError):
    """A circuit cannot be represented by or decoded from the public IR."""


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _source(span: SourceSpan, aliases: Mapping[str, str]) -> IRSource:
    wire_path = aliases.get(span.filename)
    if wire_path is None:
        source_path = Path(span.filename)
        wire_path = aliases.get(str(source_path.resolve()), source_path.as_posix())
    return IRSource(
        wire_path,
        span.start_line,
        span.start_col,
        span.end_line,
        span.end_col,
    )


@dataclass(frozen=True, slots=True)
class IRSource:
    """A one-based, Unicode-scalar-column, half-open range in the dependency closure."""

    file: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "start": [self.start_line, self.start_col],
            "end": [self.end_line, self.end_col],
        }


@dataclass(frozen=True, slots=True)
class IRParameter:
    name: str
    expression: str
    source: IRSource

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "expression": self.expression,
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class IRModel:
    id: str
    module: str
    name: str
    kind: str
    source_form: str
    source: IRSource

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "module": self.module,
            "name": self.name,
            "kind": self.kind,
            "source_form": self.source_form,
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class IRInstance:
    id: str
    name: str
    family: str
    connections: tuple[str, ...]
    model: str | None
    value: str | None
    source_form: str | None
    parameters: tuple[IRParameter, ...]
    source: IRSource

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "connections": list(self.connections),
            "model": self.model,
            "value": self.value,
            "source_form": self.source_form,
            "parameters": [item.as_dict() for item in self.parameters],
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class IRLoss:
    module: str
    reason: str
    card: str
    source: IRSource

    def as_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "reason": self.reason,
            "card": self.card,
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class IRModule:
    name: str
    ports: tuple[str, ...]
    parameters: tuple[IRParameter, ...]
    instances: tuple[IRInstance, ...]
    source: IRSource | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ports": list(self.ports),
            "parameters": [item.as_dict() for item in self.parameters],
            "instances": [item.as_dict() for item in self.instances],
            "source": self.source.as_dict() if self.source is not None else None,
        }


@dataclass(frozen=True, slots=True)
class CircuitIR:
    entry: str
    modules: tuple[IRModule, ...]
    models: tuple[IRModel, ...]
    global_nodes: tuple[str, ...]
    dependencies: tuple[str, ...]
    losses: tuple[IRLoss, ...]
    schema: str = IR_SCHEMA
    schema_version: int = IR_VERSION
    _validation_token: object | None = field(default=None, init=False, repr=False, compare=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "entry": self.entry,
            "modules": [item.as_dict() for item in self.modules],
            "models": [item.as_dict() for item in self.models],
            "global_nodes": list(self.global_nodes),
            "dependencies": list(self.dependencies),
            "losses": [item.as_dict() for item in self.losses],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_validated_json(self).encode("utf-8")).hexdigest()

    @property
    def instance_count(self) -> int:
        return sum(len(module.instances) for module in self.modules)


def _parameters(statement: Param, aliases: Mapping[str, str]) -> tuple[IRParameter, ...]:
    source = _source(statement.meta.span, aliases)
    return tuple(
        IRParameter(
            item.name,
            format_expression(item.expression),
            source,
        )
        for item in statement.assignments
    )


def _module_parameters(
    subcircuit: Subcircuit | None,
    body: Sequence[Statement],
    aliases: Mapping[str, str],
) -> tuple[IRParameter, ...]:
    values: list[IRParameter] = []
    if subcircuit is not None:
        values.extend(
            IRParameter(
                item.name,
                format_expression(item.expression),
                _source(subcircuit.span, aliases),
            )
            for item in subcircuit.defaults
        )
    for statement in body:
        if isinstance(statement, Param):
            values.extend(_parameters(statement, aliases))
    return tuple(values)


def _instance(module: str, element: Element, aliases: Mapping[str, str]) -> IRInstance:
    value = format_expression(element.value) if element.value is not None else None
    source_form = element.tail or None
    source = _source(element.meta.span, aliases)
    return IRInstance(
        id=f"{module.casefold()}::{element.name.casefold()}",
        name=element.name,
        family=element.family,
        connections=element.nodes,
        model=element.model,
        value=value,
        source_form=source_form,
        parameters=tuple(
            IRParameter(
                item.name,
                format_expression(item.expression),
                source,
            )
            for item in element.parameters
        ),
        source=source,
    )


def _one_module(
    name: str,
    ports: tuple[str, ...],
    body: Sequence[Statement],
    subcircuit: Subcircuit | None,
    aliases: Mapping[str, str],
) -> tuple[IRModule, list[IRLoss]]:
    instances = tuple(_instance(name, item, aliases) for item in body if isinstance(item, Element))
    losses = [
        IRLoss(name, item.reason, item.text, _source(item.meta.span, aliases))
        for item in body
        if isinstance(item, Opaque)
    ]
    module_source = _source(subcircuit.span, aliases) if subcircuit is not None else None
    return (
        IRModule(
            name,
            ports,
            _module_parameters(subcircuit, body, aliases),
            instances,
            module_source,
        ),
        losses,
    )


def _source_aliases(
    deck: SemanticDeck, source_files: Sequence[tuple[Path, bytes]]
) -> dict[str, str]:
    """Map checkout-specific absolute paths to stable project-relative identities."""

    root = deck.entry.parent.resolve()
    aliases: dict[str, str] = {}
    for path, content in source_files:
        resolved = path.resolve()
        try:
            relative = quote(resolved.relative_to(root).as_posix(), safe="/-._~")
        except ValueError:
            digest = hashlib.sha256(content).hexdigest()
            relative = f"external/{digest}/{quote(resolved.name, safe='-._~')}"
        aliases[str(path)] = relative
        aliases[str(resolved)] = relative
    for dependency in deck.dependencies:
        resolved = dependency.resolve()
        if str(resolved) not in aliases:
            try:
                relative = quote(resolved.relative_to(root).as_posix(), safe="/-._~")
            except ValueError as error:
                raise CircuitIRError(
                    f"external dependency {resolved} has no source snapshot"
                ) from error
            aliases[str(resolved)] = relative
    return aliases


def _models(module: str, body: Sequence[Statement], aliases: Mapping[str, str]) -> list[IRModel]:
    return [
        IRModel(
            id=f"{module.casefold()}::model::{statement.name.casefold()}",
            module=module,
            name=statement.name,
            kind=statement.kind,
            source_form=statement.tail,
            source=_source(statement.meta.span, aliases),
        )
        for statement in body
        if isinstance(statement, Model)
    ]


def build_ir(analysis: Analysis) -> CircuitIR:
    """Build a stable IR from a successfully analyzed, include-resolved deck."""

    if analysis.has_errors or analysis.deck is None:
        raise CircuitIRError("circuit IR requires an analysis without errors")
    deck = analysis.deck
    aliases = _source_aliases(deck, analysis.source_files)
    top, losses = _one_module("$top", (), deck.top, None, aliases)
    modules = [top]
    models = _models("$top", deck.top, aliases)
    for subcircuit in sorted(deck.subcircuits, key=lambda item: item.name.casefold()):
        module, module_losses = _one_module(
            subcircuit.name,
            subcircuit.pins,
            subcircuit.body,
            subcircuit,
            aliases,
        )
        modules.append(module)
        losses.extend(module_losses)
        models.extend(_models(subcircuit.name, subcircuit.body, aliases))
    models.sort(key=lambda item: (item.module.casefold(), item.name.casefold()))
    if len({model.id for model in models}) != len(models):
        raise CircuitIRError("model names must be unique within each module")
    losses.sort(key=lambda item: (item.source.file, item.source.start_line, item.module, item.card))
    result = CircuitIR(
        entry=aliases[str(deck.entry.resolve())],
        modules=tuple(modules),
        models=tuple(models),
        global_nodes=tuple(deck.global_nodes),
        dependencies=tuple(
            sorted(
                dict.fromkeys(aliases[str(path.resolve())] for path in deck.dependencies),
                key=str.casefold,
            )
        ),
        losses=tuple(losses),
    )
    if result.instance_count > MAX_INSTANCES:
        raise CircuitIRError(f"circuit IR exceeds the {MAX_INSTANCES}-instance limit")
    validate_ir(result)
    return result


def dump_ir(ir: CircuitIR, *, pretty: bool = True) -> str:
    """Serialize an IR deterministically."""

    document, canonical = _validated_document(ir)
    rendered = (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if pretty
        else canonical + "\n"
    )
    if len(rendered.encode("utf-8")) > MAX_IR_BYTES:
        raise CircuitIRError(f"serialized circuit IR exceeds the {MAX_IR_BYTES}-byte limit")
    return rendered


def _object(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CircuitIRError(f"{context} must be an object with string keys")
    return value


def _exact(value: Mapping[str, object], keys: set[str], context: str) -> None:
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise CircuitIRError(f"{context} has " + "; ".join(details))


def _string(value: object, context: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise CircuitIRError(f"{context} must be a non-empty string")
    if len(value) > MAX_STRING_CHARS:
        raise CircuitIRError(f"{context} exceeds the {MAX_STRING_CHARS}-character limit")
    if "\x00" in value:
        raise CircuitIRError(f"{context} must not contain NUL")
    if "\ufffd" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CircuitIRError(f"{context} must contain only Unicode scalar values")
    return value


def _optional_string(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context, empty=True)


def _identifier(value: object, context: str, *, top: bool = False) -> str:
    name = _string(value, context)
    if (top and name == "$top") or _IDENTIFIER.fullmatch(name):
        return name
    raise CircuitIRError(f"{context} must be a portable SPICE identifier")


def _optional_identifier(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, context)


def _token(value: object, context: str) -> str:
    token = _string(value, context)
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in token):
        raise CircuitIRError(f"{context} must be a printable ASCII SPICE token")
    return token


def _array(value: object, context: str, *, limit: int) -> list[object]:
    if not isinstance(value, list):
        raise CircuitIRError(f"{context} must be an array")
    if len(value) > limit:
        raise CircuitIRError(f"{context} exceeds the {limit}-item limit")
    return value


def _tokens(value: object, context: str, *, limit: int) -> tuple[str, ...]:
    return tuple(
        _token(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context, limit=limit))
    )


def _portable_path(value: object, context: str) -> str:
    path = _string(value, context)
    parsed = PurePosixPath(path)
    if (
        "\\" in path
        or path == "."
        or path.startswith("/")
        or ":" in path
        or parsed.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in path)
    ):
        raise CircuitIRError(f"{context} must be a normalized relative POSIX path")
    return path


def _positive_integer(value: object, context: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_SOURCE_COORDINATE
    ):
        raise CircuitIRError(f"{context} must be an integer from 1 through {MAX_SOURCE_COORDINATE}")
    return value


def _load_source(value: object, context: str) -> IRSource:
    raw = _object(value, context)
    _exact(raw, {"file", "start", "end"}, context)
    start = _array(raw["start"], f"{context}.start", limit=2)
    end = _array(raw["end"], f"{context}.end", limit=2)
    if len(start) != 2 or len(end) != 2:
        raise CircuitIRError(f"{context} ranges must contain line and column")
    source = IRSource(
        _portable_path(raw["file"], f"{context}.file"),
        _positive_integer(start[0], f"{context}.start[0]"),
        _positive_integer(start[1], f"{context}.start[1]"),
        _positive_integer(end[0], f"{context}.end[0]"),
        _positive_integer(end[1], f"{context}.end[1]"),
    )
    if (source.end_line, source.end_col) < (source.start_line, source.start_col):
        raise CircuitIRError(f"{context} end precedes its start")
    return source


def _load_optional_source(value: object, context: str) -> IRSource | None:
    if value is None:
        return None
    return _load_source(value, context)


def _load_parameter(value: object, context: str) -> IRParameter:
    raw = _object(value, context)
    _exact(raw, {"name", "expression", "source"}, context)
    name = _identifier(raw["name"], f"{context}.name")
    expression = _string(raw["expression"], f"{context}.expression")
    try:
        canonical = format_expression(parse_expression(expression))
    except (RecursionError, ValueError) as error:
        raise CircuitIRError(f"{context}.expression is invalid: {error}") from error
    if canonical != expression:
        raise CircuitIRError(f"{context}.expression is not canonical")
    return IRParameter(name, expression, _load_source(raw["source"], f"{context}.source"))


def _load_instance(value: object, context: str) -> IRInstance:
    raw = _object(value, context)
    keys = {
        "id",
        "name",
        "family",
        "connections",
        "model",
        "value",
        "source_form",
        "parameters",
        "source",
    }
    _exact(raw, keys, context)
    parameters = tuple(
        _load_parameter(item, f"{context}.parameters[{index}]")
        for index, item in enumerate(_array(raw["parameters"], f"{context}.parameters", limit=4096))
    )
    result = IRInstance(
        _string(raw["id"], f"{context}.id"),
        _identifier(raw["name"], f"{context}.name"),
        _identifier(raw["family"], f"{context}.family"),
        _tokens(raw["connections"], f"{context}.connections", limit=4096),
        _optional_identifier(raw["model"], f"{context}.model"),
        _optional_string(raw["value"], f"{context}.value"),
        _optional_string(raw["source_form"], f"{context}.source_form"),
        parameters,
        _load_source(raw["source"], f"{context}.source"),
    )
    if result.family not in _FAMILIES:
        raise CircuitIRError(f"{context}.family is not supported by circuit IR version 1")
    if result.name[0].upper() != result.family:
        raise CircuitIRError(f"{context}.name does not match its element family")
    if result.value is not None:
        try:
            canonical = format_expression(parse_expression(result.value))
        except (RecursionError, ValueError) as error:
            raise CircuitIRError(f"{context}.value is invalid: {error}") from error
        if canonical != result.value:
            raise CircuitIRError(f"{context}.value is not canonical")
    return result


def _load_module(value: object, context: str) -> IRModule:
    raw = _object(value, context)
    _exact(raw, {"name", "ports", "parameters", "instances", "source"}, context)
    parameters = tuple(
        _load_parameter(item, f"{context}.parameters[{index}]")
        for index, item in enumerate(_array(raw["parameters"], f"{context}.parameters", limit=4096))
    )
    instances = tuple(
        _load_instance(item, f"{context}.instances[{index}]")
        for index, item in enumerate(
            _array(raw["instances"], f"{context}.instances", limit=MAX_INSTANCES)
        )
    )
    return IRModule(
        _identifier(raw["name"], f"{context}.name", top=True),
        _tokens(raw["ports"], f"{context}.ports", limit=4096),
        parameters,
        instances,
        _load_optional_source(raw["source"], f"{context}.source"),
    )


def _load_model(value: object, context: str) -> IRModel:
    raw = _object(value, context)
    _exact(raw, {"id", "module", "name", "kind", "source_form", "source"}, context)
    return IRModel(
        _string(raw["id"], f"{context}.id"),
        _identifier(raw["module"], f"{context}.module", top=True),
        _identifier(raw["name"], f"{context}.name"),
        _identifier(raw["kind"], f"{context}.kind"),
        _string(raw["source_form"], f"{context}.source_form", empty=True),
        _load_source(raw["source"], f"{context}.source"),
    )


def _load_loss(value: object, context: str) -> IRLoss:
    raw = _object(value, context)
    _exact(raw, {"module", "reason", "card", "source"}, context)
    return IRLoss(
        _identifier(raw["module"], f"{context}.module", top=True),
        _string(raw["reason"], f"{context}.reason"),
        _string(raw["card"], f"{context}.card", empty=True),
        _load_source(raw["source"], f"{context}.source"),
    )


def _reject_constant(token: str) -> object:
    raise ValueError(f"non-finite number {token}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def load_ir_text(text: str) -> CircuitIR:
    """Decode strict JSON into ``CircuitIR`` without accepting ambiguous types."""

    try:
        encoded = text.encode("utf-8")
    except UnicodeError as error:
        raise CircuitIRError(f"invalid circuit IR text: {error}") from error
    if len(encoded) > MAX_IR_BYTES:
        raise CircuitIRError(f"circuit IR exceeds the {MAX_IR_BYTES}-byte limit")
    try:
        parsed: object = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise CircuitIRError(f"invalid circuit IR JSON: {error}") from error
    raw = _object(parsed, "circuit IR")
    keys = {
        "schema",
        "schema_version",
        "entry",
        "modules",
        "models",
        "global_nodes",
        "dependencies",
        "losses",
    }
    _exact(raw, keys, "circuit IR")
    if raw["schema"] != IR_SCHEMA or type(raw["schema_version"]) is not int:
        raise CircuitIRError("unsupported circuit IR schema or version")
    if raw["schema_version"] != IR_VERSION:
        raise CircuitIRError("unsupported circuit IR schema or version")
    modules = tuple(
        _load_module(item, f"modules[{index}]")
        for index, item in enumerate(_array(raw["modules"], "modules", limit=MAX_MODULES))
    )
    if not modules or modules[0].name != "$top":
        raise CircuitIRError("modules must begin with the $top module")
    if modules[0].source is not None or any(module.source is None for module in modules[1:]):
        raise CircuitIRError("only the $top module may have a null source")
    if len({module.name.casefold() for module in modules}) != len(modules):
        raise CircuitIRError("module names must be unique ignoring case")
    for module in modules:
        if len({port.casefold() for port in module.ports}) != len(module.ports):
            raise CircuitIRError(f"module {module.name!r} contains duplicate ports")
        if len({parameter.name.casefold() for parameter in module.parameters}) != len(
            module.parameters
        ):
            raise CircuitIRError(f"module {module.name!r} contains duplicate parameters")
        if len({instance.name.casefold() for instance in module.instances}) != len(
            module.instances
        ):
            raise CircuitIRError(f"module {module.name!r} contains duplicate instances")
        for instance in module.instances:
            if len({item.name.casefold() for item in instance.parameters}) != len(
                instance.parameters
            ):
                raise CircuitIRError(f"instance {instance.name!r} contains duplicate parameters")
    all_instances = tuple(instance for module in modules for instance in module.instances)
    if len(all_instances) > MAX_INSTANCES:
        raise CircuitIRError(f"circuit IR exceeds the {MAX_INSTANCES}-instance limit")
    if len({instance.id for instance in all_instances}) != len(all_instances):
        raise CircuitIRError("instance IDs must be globally unique")
    for module in modules:
        for instance in module.instances:
            expected = f"{module.name.casefold()}::{instance.name.casefold()}"
            if instance.id != expected:
                raise CircuitIRError(
                    f"instance {instance.name!r} in module {module.name!r} has a non-canonical ID"
                )
    models = tuple(
        _load_model(item, f"models[{index}]")
        for index, item in enumerate(_array(raw["models"], "models", limit=MAX_ITEMS))
    )
    module_names = {module.name.casefold() for module in modules}
    if len({model.id for model in models}) != len(models):
        raise CircuitIRError("model IDs must be globally unique")
    for model in models:
        if model.module.casefold() not in module_names:
            raise CircuitIRError(f"model {model.name!r} refers to an unknown module")
        expected = f"{model.module.casefold()}::model::{model.name.casefold()}"
        if model.id != expected:
            raise CircuitIRError(f"model {model.name!r} has a non-canonical ID")
    modules_by_name = {module.name.casefold(): module for module in modules}
    models_by_scope = {(model.module.casefold(), model.name.casefold()) for model in models}
    for module in modules:
        for instance in module.instances:
            if instance.family in {"R", "C", "L"}:
                if (
                    len(instance.connections) != 2
                    or instance.value is None
                    or instance.model is not None
                    or instance.source_form is not None
                ):
                    raise CircuitIRError(
                        f"instance {instance.name!r} has invalid {instance.family} fields"
                    )
            elif instance.family in {"V", "I"}:
                if (
                    len(instance.connections) != 2
                    or not instance.source_form
                    or instance.value is not None
                    or instance.model is not None
                ):
                    raise CircuitIRError(
                        f"instance {instance.name!r} has invalid {instance.family} fields"
                    )
            elif instance.family == "M":
                if (
                    len(instance.connections) != 4
                    or instance.model is None
                    or instance.value is not None
                    or instance.source_form is not None
                ):
                    raise CircuitIRError(f"instance {instance.name!r} has invalid M fields")
                model_name = instance.model.casefold()
                if (module.name.casefold(), model_name) not in models_by_scope and (
                    "$top",
                    model_name,
                ) not in models_by_scope:
                    raise CircuitIRError(f"instance {instance.name!r} refers to an unknown model")
            else:
                if (
                    instance.model is None
                    or instance.value is not None
                    or instance.source_form is not None
                ):
                    raise CircuitIRError(f"instance {instance.name!r} has invalid X fields")
                target = modules_by_name.get(instance.model.casefold())
                if target is None:
                    raise CircuitIRError(
                        f"instance {instance.name!r} refers to an unknown subcircuit"
                    )
                if len(instance.connections) != len(target.ports):
                    raise CircuitIRError(
                        f"instance {instance.name!r} does not match its subcircuit port count"
                    )
    result = CircuitIR(
        entry=_portable_path(raw["entry"], "entry"),
        modules=modules,
        models=models,
        global_nodes=_tokens(raw["global_nodes"], "global_nodes", limit=MAX_ITEMS),
        dependencies=tuple(
            _portable_path(item, f"dependencies[{index}]")
            for index, item in enumerate(
                _array(raw["dependencies"], "dependencies", limit=MAX_ITEMS)
            )
        ),
        losses=tuple(
            _load_loss(item, f"losses[{index}]")
            for index, item in enumerate(_array(raw["losses"], "losses", limit=MAX_ITEMS))
        ),
    )
    if len({node.casefold() for node in result.global_nodes}) != len(result.global_nodes):
        raise CircuitIRError("global node names must be unique ignoring case")
    dependency_keys = {dependency.lower() for dependency in result.dependencies}
    if len(dependency_keys) != len(result.dependencies):
        raise CircuitIRError("dependencies must be unique ignoring ASCII case")
    if result.entry.lower() not in dependency_keys:
        raise CircuitIRError("entry must be present in dependencies")
    if any(loss.module.casefold() not in module_names for loss in result.losses):
        raise CircuitIRError("loss refers to an unknown module")
    source_files = {
        instance.source.file for module in result.modules for instance in module.instances
    }
    source_files.update(
        parameter.source.file for module in result.modules for parameter in module.parameters
    )
    source_files.update(
        parameter.source.file
        for module in result.modules
        for instance in module.instances
        for parameter in instance.parameters
    )
    source_files.update(
        module.source.file for module in result.modules if module.source is not None
    )
    source_files.update(model.source.file for model in result.models)
    source_files.update(loss.source.file for loss in result.losses)
    if not {source.lower() for source in source_files}.issubset(dependency_keys):
        raise CircuitIRError("source location refers to a file outside dependencies")
    object.__setattr__(result, "_validation_token", _VALIDATED_IR_TOKEN)
    return result


def _validated_document(ir: CircuitIR) -> tuple[Mapping[str, object], str]:
    try:
        document = ir.as_dict()
        canonical = _canonical_json(document)
    except (AttributeError, TypeError, ValueError) as error:
        raise CircuitIRError(f"invalid in-memory circuit IR: {error}") from error
    if ir._validation_token is _VALIDATED_IR_TOKEN:
        return document, canonical
    decoded = load_ir_text(canonical)
    if decoded != ir:
        raise CircuitIRError("in-memory circuit IR uses non-canonical value types")
    object.__setattr__(ir, "_validation_token", _VALIDATED_IR_TOKEN)
    return document, canonical


def _validated_json(ir: CircuitIR) -> str:
    return _validated_document(ir)[1]


def validate_ir(ir: CircuitIR) -> None:
    """Validate an in-memory IR with the same rules as an untrusted document."""

    _validated_json(ir)


def load_ir(path: str | Path) -> CircuitIR:
    source = Path(path)
    try:
        with source.open("rb") as stream:
            payload = stream.read(MAX_IR_BYTES + 1)
    except OSError as error:
        raise CircuitIRError(f"cannot read circuit IR: {error}") from error
    if len(payload) > MAX_IR_BYTES:
        raise CircuitIRError(f"circuit IR exceeds the {MAX_IR_BYTES}-byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise CircuitIRError(f"cannot read circuit IR: {error}") from error
    return load_ir_text(text)


def write_ir(
    ir: CircuitIR,
    path: str | Path,
    *,
    pretty: bool = True,
    force: bool = False,
) -> None:
    """Atomically write IR, refusing to replace an existing path unless forced."""

    write_text_atomic(path, dump_ir(ir, pretty=pretty), force=force)
