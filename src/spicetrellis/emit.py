"""Stable SPICE, diagnostic, and JSON renderers."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from spicetrellis.expressions import spice_expression
from spicetrellis.model import (
    Assignment,
    Blank,
    Comment,
    Control,
    Diagnostic,
    Directive,
    Element,
    End,
    Func,
    Global,
    Include,
    LibCall,
    LibSectionEnd,
    LibSectionStart,
    Model,
    Opaque,
    Option,
    Param,
    Statement,
    SubcktEnd,
    SubcktStart,
    SyntaxDeck,
    Title,
)


def _assignment(assignment: Assignment) -> str:
    return f"{assignment.name}={spice_expression(assignment.expression)}"


def _commented(rendered: str, statement: Statement) -> str:
    comment = statement.meta.inline_comment
    if comment is None:
        return rendered
    marker = statement.meta.comment_marker or "$"
    return f"{rendered} {marker} {comment}"


def _quoted(value: str) -> str:
    """Quote a path only when it needs it, so round-tripping stays byte-stable."""

    return f'"{value}"' if any(character.isspace() for character in value) else value


def format_statement(statement: Statement) -> str:
    if isinstance(statement, Blank):
        return ""
    if isinstance(statement, Comment):
        text = statement.text
        if not text:
            return "*"
        return f"*{text}" if text.startswith("*") else f"* {text}"
    if isinstance(statement, Include):
        return _commented(f".include {_quoted(statement.target)}", statement)
    if isinstance(statement, LibCall):
        return _commented(f".lib {_quoted(statement.target)} {statement.section}", statement)
    if isinstance(statement, LibSectionStart):
        return _commented(f".lib {statement.name}", statement)
    if isinstance(statement, LibSectionEnd):
        rendered = ".endl" + (f" {statement.name}" if statement.name else "")
        return _commented(rendered, statement)
    if isinstance(statement, Param):
        rendered = ".param " + " ".join(_assignment(item) for item in statement.assignments)
        return _commented(rendered, statement)
    if isinstance(statement, SubcktStart):
        parts = [".subckt", statement.name, *statement.pins]
        parts.extend(_assignment(item) for item in statement.defaults)
        return _commented(" ".join(parts), statement)
    if isinstance(statement, SubcktEnd):
        rendered = ".ends" + (f" {statement.name}" if statement.name else "")
        return _commented(rendered, statement)
    if isinstance(statement, Model):
        rendered = " ".join(
            part for part in [".model", statement.name, statement.kind, statement.tail] if part
        )
        return _commented(rendered, statement)
    if isinstance(statement, Global):
        return _commented(".global " + " ".join(statement.nodes), statement)
    if isinstance(statement, End):
        return _commented(".end", statement)
    if isinstance(statement, Opaque):
        return _commented(statement.text, statement)
    if isinstance(statement, Title):
        return statement.text
    if isinstance(statement, Directive):
        return _commented(statement.text, statement)
    if isinstance(statement, Control):
        return "\n".join(line.strip() for line in statement.lines)
    if isinstance(statement, Option):
        settings = " ".join(
            key if value is None else f"{key}={value}" for key, value in statement.settings
        )
        return _commented(f"{statement.keyword} {settings}".rstrip(), statement)
    if isinstance(statement, Func):
        arguments = ", ".join(statement.parameters)
        rendered = f".func {statement.name}({arguments}) {spice_expression(statement.body)}"
        return _commented(rendered, statement)
    return _commented(format_element(statement), statement)


def format_element(element: Element) -> str:
    """Render one device card in the field order its family expects."""

    family = element.family
    parts: list[str] = [element.name]
    value = spice_expression(element.value) if element.value is not None else ""
    if family in {"E", "G"} and element.form.startswith("poly"):
        parts += [*element.nodes[:2], element.form, *element.nodes[2:], element.tail]
    elif family in {"F", "H"} and element.form.startswith("poly"):
        parts += [*element.nodes, element.form, *element.controls, element.tail]
    elif family in {"F", "H"}:
        parts += [*element.nodes, *element.controls, value]
    elif family == "K":
        parts += [*element.controls, value]
    elif family == "W":
        parts += [*element.nodes, *element.controls, element.model or "", element.tail]
    else:
        parts += list(element.nodes)
        if family in {"R", "C", "L"}:
            parts += [value, element.model or ""]
        elif family in {"V", "I"}:
            parts.append(element.tail)
        elif family in {"E", "G"}:
            parts.append(value)
        elif family != "B" and family != "T":
            parts += [element.model or "", value, element.tail]
    parts.extend(_assignment(item) for item in element.parameters)
    return " ".join(part for part in parts if part)


def format_deck(deck: SyntaxDeck | tuple[Statement, ...]) -> str:
    statements = deck.statements if isinstance(deck, SyntaxDeck) else deck
    return "\n".join(format_statement(statement) for statement in statements).rstrip() + "\n"


def _display_path(filename: str) -> str:
    """Show a path relative to the working directory when it lies inside it."""

    try:
        return Path(filename).relative_to(Path.cwd()).as_posix()
    except ValueError:
        return filename


def format_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> str:
    if not diagnostics:
        return "no diagnostics\n"
    return "".join(
        f"{_display_path(item.primary.filename)}:{item.primary.start_line}:"
        f"{item.primary.start_col}: {item.severity} {item.code}: {item.message}\n"
        for item in diagnostics
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if is_dataclass(value):
        result = {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
        result["type"] = type(value).__name__
        return result
    return value


def to_json(value: Any) -> str:
    return json.dumps(_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
