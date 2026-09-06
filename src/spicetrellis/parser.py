"""Parser for the explicit portable-SPICE subset."""

from __future__ import annotations

import re

from spicetrellis.expressions import ExpressionError, parse_expression
from spicetrellis.model import (
    Assignment,
    Blank,
    CardMeta,
    Comment,
    Diagnostic,
    Element,
    End,
    Global,
    Include,
    LibCall,
    LibSectionEnd,
    LibSectionStart,
    Model,
    Opaque,
    Param,
    Statement,
    SubcktEnd,
    SubcktStart,
    SyntaxDeck,
    sorted_diagnostics,
)
from spicetrellis.source import LogicalCard, logical_cards

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.$:-]*\Z")


class CardParseError(ValueError):
    pass


def split_fields(text: str) -> list[str]:
    """Split whitespace-delimited fields while respecting quotes/braces/parentheses."""

    fields: list[str] = []
    start: int | None = None
    quote: str | None = None
    escaped = False
    braces = 0
    parentheses = 0
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            if start is None:
                start = index
        elif char == "{":
            braces += 1
            if start is None:
                start = index
        elif char == "}":
            braces -= 1
            if braces < 0:
                raise CardParseError("unmatched closing brace")
        elif char == "(":
            parentheses += 1
            if start is None:
                start = index
        elif char == ")":
            parentheses -= 1
            if parentheses < 0:
                raise CardParseError("unmatched closing parenthesis")
        elif char.isspace() and braces == 0 and parentheses == 0:
            if start is not None:
                fields.append(text[start:index])
                start = None
        elif start is None:
            start = index
    if quote:
        raise CardParseError("unterminated quoted string")
    if braces:
        raise CardParseError("unbalanced braces")
    if parentheses:
        raise CardParseError("unbalanced parentheses")
    if start is not None:
        fields.append(text[start:])
    return fields


def _meta(card: LogicalCard) -> CardMeta:
    return CardMeta(
        card.span,
        card.segments,
        card.original,
        card.inline_comment,
        card.comment_marker,
    )


def _assignments(fields: list[str], *, require_all: bool = True) -> tuple[Assignment, ...]:
    result: list[Assignment] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        if "=" in field:
            name, raw = field.split("=", 1)
            if not raw:
                index += 1
                if index >= len(fields):
                    raise CardParseError(f"assignment {name!r} has no value")
                raw = fields[index]
        elif index + 2 < len(fields) and fields[index + 1] == "=":
            name = field
            raw = fields[index + 2]
            index += 2
        else:
            if require_all:
                raise CardParseError(f"expected name=value assignment, got {field!r}")
            break
        if not _IDENTIFIER.fullmatch(name):
            raise CardParseError(f"invalid parameter name {name!r}")
        try:
            expression = parse_expression(raw)
        except ExpressionError as error:
            raise CardParseError(f"invalid expression for {name}: {error}") from error
        result.append(Assignment(name, expression, raw))
        index += 1
    return tuple(result)


def _first_assignment_index(fields: list[str]) -> int:
    for index, field in enumerate(fields):
        if "=" in field or (index + 1 < len(fields) and fields[index + 1] == "="):
            return index
    return len(fields)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_element(fields: list[str], meta: CardMeta) -> Element | Opaque:
    name = fields[0]
    family = name[0].upper()
    if family in {"R", "C", "L"}:
        if len(fields) < 4:
            raise CardParseError(f"{family} element requires name, two nodes, and value")
        try:
            value = parse_expression(fields[3])
        except ExpressionError as error:
            raise CardParseError(f"invalid {family} value: {error}") from error
        parameters = _assignments(fields[4:]) if len(fields) > 4 else ()
        return Element(name, family, tuple(fields[1:3]), "", parameters, value, None, meta)
    if family in {"V", "I"}:
        if len(fields) < 4:
            raise CardParseError(f"{family} source requires name, two nodes, and source value")
        return Element(name, family, tuple(fields[1:3]), " ".join(fields[3:]), (), None, None, meta)
    if family == "M":
        if len(fields) < 6:
            raise CardParseError("MOS element requires name, four nodes, and model")
        return Element(
            name,
            family,
            tuple(fields[1:5]),
            "",
            _assignments(fields[6:]) if len(fields) > 6 else (),
            None,
            fields[5],
            meta,
        )
    if family == "X":
        boundary = _first_assignment_index(fields[1:]) + 1
        positional = fields[1:boundary]
        if len(positional) < 2:
            raise CardParseError(
                "subcircuit instance requires at least one node and a subcircuit name"
            )
        parameters = _assignments(fields[boundary:]) if boundary < len(fields) else ()
        return Element(
            name,
            family,
            tuple(positional[:-1]),
            "",
            parameters,
            None,
            positional[-1],
            meta,
        )
    return Opaque(" ".join(fields), f"unsupported element family {family}", meta)


def _parse_card(card: LogicalCard) -> Statement:
    meta = _meta(card)
    if card.kind == "blank":
        return Blank(meta)
    if card.kind == "comment":
        return Comment(card.code, meta)
    if card.kind == "orphan-continuation":
        return Opaque(card.code, "orphan continuation", meta)
    fields = split_fields(card.code)
    if not fields:
        return Blank(meta)
    first = fields[0]
    lowered = first.casefold()
    if first.startswith("."):
        if lowered == ".include":
            if len(fields) != 2:
                raise CardParseError(".include requires exactly one quoted or unquoted path")
            return Include(_unquote(fields[1]), meta)
        if lowered == ".lib":
            # Two arguments name a file and a section; one argument opens a
            # section. The one-argument *call* form some dialects allow is
            # refused rather than guessed, because reading it as a section
            # opening -- or the reverse -- would change which models are used
            # without saying so.
            if len(fields) == 3:
                return LibCall(_unquote(fields[1]), _unquote(fields[2]), meta)
            if len(fields) == 2:
                section = _unquote(fields[1])
                if not _IDENTIFIER.fullmatch(section):
                    raise CardParseError(".lib requires a valid section name")
                return LibSectionStart(section, meta)
            raise CardParseError(".lib requires a section name, or a path and a section name")
        if lowered == ".endl":
            if len(fields) > 2:
                raise CardParseError(".endl accepts at most one section name")
            return LibSectionEnd(_unquote(fields[1]) if len(fields) == 2 else None, meta)
        if lowered == ".param":
            if len(fields) < 2:
                raise CardParseError(".param requires at least one assignment")
            return Param(_assignments(fields[1:]), meta)
        if lowered == ".subckt":
            if len(fields) < 2 or not _IDENTIFIER.fullmatch(fields[1]):
                raise CardParseError(".subckt requires a valid name")
            boundary = _first_assignment_index(fields[2:]) + 2
            return SubcktStart(
                fields[1],
                tuple(fields[2:boundary]),
                _assignments(fields[boundary:]) if boundary < len(fields) else (),
                meta,
            )
        if lowered == ".ends":
            if len(fields) > 2:
                raise CardParseError(".ends accepts at most one subcircuit name")
            return SubcktEnd(fields[1] if len(fields) == 2 else None, meta)
        if lowered == ".model":
            if len(fields) < 3:
                raise CardParseError(".model requires a name and model kind")
            return Model(fields[1], fields[2], " ".join(fields[3:]), meta)
        if lowered == ".global":
            if len(fields) < 2:
                raise CardParseError(".global requires at least one node")
            return Global(tuple(fields[1:]), meta)
        if lowered == ".end":
            if len(fields) != 1:
                raise CardParseError(".end does not accept arguments in the portable subset")
            return End(meta)
        return Opaque(card.code, f"unsupported directive {first}", meta)
    if not _IDENTIFIER.fullmatch(first):
        raise CardParseError(f"invalid element name {first!r}")
    return _parse_element(fields, meta)


def parse_text(text: str, filename: str = "<memory>") -> SyntaxDeck:
    cards, diagnostics = logical_cards(text, filename)
    statements: list[Statement] = []
    ended = False
    for card in cards:
        if ended:
            if card.kind not in {"blank", "comment"}:
                diagnostics.append(
                    Diagnostic(
                        "ST1003",
                        "error",
                        "statement after .end is ignored",
                        card.span,
                    )
                )
            continue
        try:
            statement = _parse_card(card)
            statements.append(statement)
            ended = isinstance(statement, End)
            if isinstance(statement, Opaque):
                diagnostics.append(
                    Diagnostic("ST1101", "warning", statement.reason, statement.meta.span)
                )
        except CardParseError as error:
            diagnostics.append(Diagnostic("ST1002", "error", str(error), card.span))
            statements.append(Opaque(card.code, str(error), _meta(card)))
        except RecursionError:
            reason = "statement exceeds the parser nesting limit"
            diagnostics.append(Diagnostic("ST1004", "error", reason, card.span))
            statements.append(Opaque(card.code, reason, _meta(card)))
    return SyntaxDeck(filename, tuple(statements), sorted_diagnostics(diagnostics))
