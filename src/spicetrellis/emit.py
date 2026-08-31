"""Stable SPICE, diagnostic, and JSON renderers."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from spicetrellis.expressions import format_expression
from spicetrellis.model import (
    Assignment,
    Blank,
    Comment,
    Diagnostic,
    End,
    Global,
    Include,
    Model,
    Opaque,
    Param,
    Statement,
    SubcktEnd,
    SubcktStart,
    SyntaxDeck,
)


def _assignment(assignment: Assignment) -> str:
    return f"{assignment.name}={format_expression(assignment.expression)}"


def _commented(rendered: str, statement: Statement) -> str:
    comment = statement.meta.inline_comment
    if comment is None:
        return rendered
    marker = statement.meta.comment_marker or "$"
    return f"{rendered} {marker} {comment}"


def format_statement(statement: Statement) -> str:
    if isinstance(statement, Blank):
        return ""
    if isinstance(statement, Comment):
        return "*" if not statement.text else f"* {statement.text}"
    if isinstance(statement, Include):
        target = (
            f'"{statement.target}"'
            if any(character.isspace() for character in statement.target)
            else statement.target
        )
        return _commented(f".include {target}", statement)
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
    parts = [statement.name, *statement.nodes]
    if statement.family in {"R", "C", "L"} and statement.value is not None:
        parts.append(format_expression(statement.value))
    elif statement.family in {"V", "I"}:
        parts.append(statement.tail)
    elif statement.family in {"M", "X"} and statement.model:
        parts.append(statement.model)
    parts.extend(_assignment(item) for item in statement.parameters)
    return _commented(" ".join(part for part in parts if part), statement)


def format_deck(deck: SyntaxDeck | tuple[Statement, ...]) -> str:
    statements = deck.statements if isinstance(deck, SyntaxDeck) else deck
    return "\n".join(format_statement(statement) for statement in statements).rstrip() + "\n"


def format_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> str:
    if not diagnostics:
        return "no diagnostics\n"
    return "".join(
        f"{item.primary.filename}:{item.primary.start_line}:{item.primary.start_col}: "
        f"{item.severity} {item.code}: {item.message}\n"
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
