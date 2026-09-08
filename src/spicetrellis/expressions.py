"""A deliberately small, deterministic SPICE parameter-expression parser."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import TypeAlias


class ExpressionError(ValueError):
    """Raised when an expression is outside the supported portable subset."""


class ExpressionLimitError(ExpressionError):
    """Raised when an otherwise parseable expression exceeds a resource limit."""


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
    r"(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)(?:meg|mil|[tgkmunpf])?",
    re.IGNORECASE | re.ASCII,
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
_MAX_EXPRESSION_DEPTH = 256
_MAX_EXPRESSION_NODES = 512
_MAX_EXPRESSION_TOKENS = 1_024
_MAX_NUMBER_CHARS = 128


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    offset: int


def _tokens(text: str) -> list[_Token]:
    result: list[_Token] = []
    index = 0
    while index < len(text):
        if text[index] in " \t\r\n":
            index += 1
            continue
        if len(result) >= _MAX_EXPRESSION_TOKENS:
            raise ExpressionLimitError(
                f"expression exceeds the {_MAX_EXPRESSION_TOKENS}-token limit"
            )
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
        if text[index] in "+-*/^(){}":
            kind = "paren" if text[index] in "(){}" else "operator"
            normalized = {"{": "(", "}": ")"}.get(text[index], text[index])
            result.append(_Token(kind, normalized, index))
            index += 1
            continue
        raise ExpressionError(f"unsupported character {text[index]!r} at offset {index}")
    result.append(_Token("eof", "", len(text)))
    return result


def _number_value(text: str) -> Decimal:
    if len(text) > _MAX_NUMBER_CHARS:
        raise ExpressionLimitError(
            f"number literal exceeds the {_MAX_NUMBER_CHARS}-character limit"
        )
    match = re.fullmatch(
        r"((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)(meg|mil|[tgkmunpf])?",
        text,
        re.IGNORECASE | re.ASCII,
    )
    if not match:
        raise ExpressionError(f"invalid number {text!r}")
    exponent = re.search(r"[eE][+-]?([0-9]+)", match.group(1), re.ASCII)
    if exponent is not None and (
        len(exponent.group(1)) > 5 or int(exponent.group(1)) > _MAX_EXPONENT_MAGNITUDE
    ):
        raise ExpressionLimitError("number exponent magnitude exceeds 10000")
    try:
        value = Decimal(match.group(1))
    except InvalidOperation as error:
        raise ExpressionError(f"invalid number {text!r}") from error
    suffix = match.group(2)
    try:
        result = value * _SCALES[suffix.casefold()] if suffix else value
    except InvalidOperation as error:
        raise ExpressionError(f"invalid number {text!r}") from error
    if not result.is_finite():
        raise ExpressionError(f"invalid number {text!r}")
    return result


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.index = 0
        self.nodes = 0

    def account_node(self, expression: Expr) -> Expr:
        self.nodes += 1
        if self.nodes > _MAX_EXPRESSION_NODES:
            raise ExpressionLimitError(f"expression exceeds the {_MAX_EXPRESSION_NODES}-node limit")
        return expression

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
        expression = self.parse_precedence(0, 0)
        if self.current.kind != "eof":
            raise ExpressionError(
                f"unexpected token {self.current.text!r} at offset {self.current.offset}"
            )
        return expression

    def parse_precedence(self, minimum: int, depth: int) -> Expr:
        if depth > _MAX_EXPRESSION_DEPTH:
            raise ExpressionLimitError(f"expression nesting exceeds {_MAX_EXPRESSION_DEPTH} levels")
        token = self.consume()
        if token.kind == "number":
            left: Expr = self.account_node(Number(_number_value(token.text), token.text))
        elif token.kind == "name":
            left = self.account_node(Name(token.text))
        elif token.text in {"+", "-"}:
            left = self.account_node(Unary(token.text, self.parse_precedence(30, depth + 1)))
        elif token.text == "(":
            left = self.parse_precedence(0, depth + 1)
            if self.current.text != ")":
                raise ExpressionError("missing closing parenthesis")
            self.consume()
        else:
            raise ExpressionError(f"unexpected token {token.text!r} at offset {token.offset}")

        precedence = {"+": 10, "-": 10, "*": 20, "/": 20, "^": 30, "**": 30}
        while self.current.text in precedence and precedence[self.current.text] >= minimum:
            operator = self.consume().text
            level = precedence[operator]
            right = self.parse_precedence(
                level if operator in {"^", "**"} else level + 1,
                depth + 1,
            )
            left = self.account_node(Binary(operator, left, right))
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
    """Render an expression in linear time without recursive string concatenation."""

    rendered: list[str] = []
    pending: list[Expr | str] = [expression]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            rendered.append(item)
        elif isinstance(item, Number):
            rendered.append(item.original)
        elif isinstance(item, Name):
            rendered.append(item.value)
        elif isinstance(item, Unary):
            pending.extend((item.operand, item.operator))
        else:
            pending.extend(("}", item.right, f" {item.operator} ", item.left, "{"))
    return "".join(rendered)
