"""A deliberately small, deterministic SPICE parameter-expression parser."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import TypeAlias


class ExpressionError(ValueError):
    """Raised when an expression is outside the supported portable subset."""


@dataclass(frozen=True, slots=True)
class Number:
    value: Decimal
    original: str


@dataclass(frozen=True, slots=True)
class Name:
    value: str


@dataclass(frozen=True, slots=True)
class Unary:
    operator: str
    operand: Expr


@dataclass(frozen=True, slots=True)
class Binary:
    operator: str
    left: Expr
    right: Expr


Expr: TypeAlias = Number | Name | Unary | Binary

_NUMBER = re.compile(
    r"(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:meg|mil|[tgkmunpf])?",
    re.IGNORECASE,
)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]*")
_SCALES = {
    "t": Decimal("1e12"),
    "g": Decimal("1e9"),
    "meg": Decimal("1e6"),
    "k": Decimal("1e3"),
    "m": Decimal("1e-3"),
    "mil": Decimal("2.54e-5"),
    "u": Decimal("1e-6"),
    "n": Decimal("1e-9"),
    "p": Decimal("1e-12"),
    "f": Decimal("1e-15"),
}
_MAX_EXPONENT_MAGNITUDE = Decimal("10000")


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    offset: int


def _tokens(text: str) -> list[_Token]:
    result: list[_Token] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        number = _NUMBER.match(text, index)
        if number:
            result.append(_Token("number", number.group(0), index))
            index = number.end()
            continue
        name = _NAME.match(text, index)
        if name:
            result.append(_Token("name", name.group(0), index))
            index = name.end()
            continue
        if text.startswith("**", index):
            result.append(_Token("operator", "**", index))
            index += 2
            continue
        if text[index] in "+-*/^()":
            kind = "paren" if text[index] in "()" else "operator"
            result.append(_Token(kind, text[index], index))
            index += 1
            continue
        raise ExpressionError(f"unsupported character {text[index]!r} at offset {index}")
    result.append(_Token("eof", "", len(text)))
    return result


def _number_value(text: str) -> Decimal:
    match = re.fullmatch(
        r"((?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(meg|mil|[tgkmunpf])?",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ExpressionError(f"invalid number {text!r}")
    value = Decimal(match.group(1))
    suffix = match.group(2)
    return value * _SCALES[suffix.casefold()] if suffix else value


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def consume(self) -> _Token:
        token = self.current
        self.index += 1
        return token

    def parse(self) -> Expr:
        if self.current.kind == "eof":
            raise ExpressionError("empty expression")
        expression = self.parse_precedence(0)
        if self.current.kind != "eof":
            raise ExpressionError(
                f"unexpected token {self.current.text!r} at offset {self.current.offset}"
            )
        return expression

    def parse_precedence(self, minimum: int) -> Expr:
        token = self.consume()
        if token.kind == "number":
            left: Expr = Number(_number_value(token.text), token.text)
        elif token.kind == "name":
            left = Name(token.text)
        elif token.text in {"+", "-"}:
            left = Unary(token.text, self.parse_precedence(30))
        elif token.text == "(":
            left = self.parse_precedence(0)
            if self.current.text != ")":
                raise ExpressionError("missing closing parenthesis")
            self.consume()
        else:
            raise ExpressionError(f"unexpected token {token.text!r} at offset {token.offset}")

        precedence = {"+": 10, "-": 10, "*": 20, "/": 20, "^": 30, "**": 30}
        while self.current.text in precedence and precedence[self.current.text] >= minimum:
            operator = self.consume().text
            level = precedence[operator]
            right = self.parse_precedence(level if operator in {"^", "**"} else level + 1)
            left = Binary(operator, left, right)
        return left


def parse_expression(text: str) -> Expr:
    """Parse braces and the supported arithmetic subset into an immutable AST."""

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        stripped = stripped[1:-1].strip()
    return _Parser(stripped).parse()


def expression_names(expression: Expr) -> frozenset[str]:
    if isinstance(expression, Name):
        return frozenset({expression.value.casefold()})
    if isinstance(expression, Number):
        return frozenset()
    if isinstance(expression, Unary):
        return expression_names(expression.operand)
    return expression_names(expression.left) | expression_names(expression.right)


def evaluate_expression(expression: Expr, environment: Mapping[str, Decimal]) -> Decimal:
    """Evaluate an expression without Python eval or user-defined functions."""

    if isinstance(expression, Number):
        return expression.value
    if isinstance(expression, Name):
        key = expression.value.casefold()
        if key not in environment:
            raise ExpressionError(f"undefined parameter {expression.value!r}")
        return environment[key]
    if isinstance(expression, Unary):
        value = evaluate_expression(expression.operand, environment)
        return value if expression.operator == "+" else -value
    left = evaluate_expression(expression.left, environment)
    right = evaluate_expression(expression.right, environment)
    try:
        if expression.operator == "+":
            return left + right
        if expression.operator == "-":
            return left - right
        if expression.operator == "*":
            return left * right
        if expression.operator == "/":
            return left / right
        if right != right.to_integral_value():
            raise ExpressionError("SPICE exponent must be an integer in the portable subset")
        if abs(right) > _MAX_EXPONENT_MAGNITUDE:
            raise ExpressionError("SPICE exponent magnitude exceeds the portable subset limit")
        return left ** int(right)
    except (DivisionByZero, InvalidOperation, OverflowError, ValueError) as error:
        raise ExpressionError(f"cannot evaluate expression: {error}") from error


def format_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    normalized = value.normalize()
    rendered = format(normalized, "f")
    if len(rendered) > 32:
        rendered = format(normalized, "e").replace("E", "e")
        rendered = rendered.replace("e+", "e")
    return rendered


def format_expression(expression: Expr) -> str:
    if isinstance(expression, Number):
        return expression.original
    if isinstance(expression, Name):
        return expression.value
    if isinstance(expression, Unary):
        return f"{expression.operator}{format_expression(expression.operand)}"
    return (
        "{"
        + format_expression(expression.left)
        + f" {expression.operator} "
        + format_expression(expression.right)
        + "}"
    )
