"""SPICE parameter expressions: parsing, evaluation and partial evaluation.

The grammar is the one ngspice's parameter evaluator accepts in ``{...}`` and
``'...'`` expressions: numbers with scale suffixes, parameter names, the usual
arithmetic operators, comparisons, ``&&``/``||``/``!``, the ternary ``c ? a : b``
and function calls.  Nothing is ever handed to Python ``eval``.

Some quantities only exist while a simulator runs: node voltages ``v(a,b)``,
branch currents ``i(vx)``, ``temper``, ``time`` and the statistical functions
(``agauss`` and friends).  :func:`fold_expression` keeps those symbolic and folds
everything else, so a flattened deck still varies with temperature and still
draws Monte Carlo samples.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from typing import TypeAlias


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or evaluated."""


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


@dataclass(frozen=True, slots=True)
class Ternary:
    condition: Expr
    if_true: Expr
    if_false: Expr


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    arguments: tuple[Expr, ...]


Expr: TypeAlias = Number | Name | Unary | Binary | Ternary | Call


@dataclass(frozen=True, slots=True)
class FunctionDef:
    """A user function from ``.func name(a, b) {body}``."""

    name: str
    parameters: tuple[str, ...]
    body: Expr


# Names whose value is only known inside a running simulation.
RUNTIME_NAMES = frozenset({"temper", "time", "hertz"})
# Functions whose arguments are circuit names, not parameters.
PROBE_FUNCTIONS = frozenset({"v", "i"})
# Functions that draw random numbers; folding them would remove Monte Carlo spread.
STATISTICAL_FUNCTIONS = frozenset({"agauss", "gauss", "aunif", "unif", "limit"})
RUNTIME_FUNCTIONS = PROBE_FUNCTIONS | STATISTICAL_FUNCTIONS

_NUMBER = re.compile(
    r"((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)(meg|mil|[tgkmunpfa])?([A-Za-z]*)",
    re.IGNORECASE | re.ASCII,
)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]*", re.ASCII)
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
    "a": Decimal("1e-18"),
}
_MAX_EXPONENT_MAGNITUDE = Decimal("10000")
_MAX_EXPRESSION_DEPTH = 256
_MAX_EXPRESSION_NODES = 512
_MAX_EXPRESSION_TOKENS = 1_024
_MAX_NUMBER_CHARS = 128
_MAX_FUNCTION_DEPTH = 32

_OPERATORS = ("**", "<=", ">=", "==", "!=", "&&", "||", "+", "-", "*", "/", "^", "<", ">", "!")
_BINARY_PRECEDENCE = {
    "||": 2,
    "&&": 3,
    "==": 4,
    "!=": 4,
    "<": 5,
    ">": 5,
    "<=": 5,
    ">=": 5,
    "+": 10,
    "-": 10,
    "*": 20,
    "/": 20,
    "^": 30,
    "**": 30,
}
_TERNARY_PRECEDENCE = 1
_UNARY_PRECEDENCE = 30


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
        for operator in _OPERATORS:
            if text.startswith(operator, index):
                result.append(_Token("operator", operator, index))
                index += len(operator)
                break
        else:
            character = text[index]
            if character in "(){}":
                normalized = {"{": "(", "}": ")"}.get(character, character)
                result.append(_Token("paren", normalized, index))
            elif character in ",?:":
                result.append(_Token("punct", character, index))
            else:
                raise ExpressionError(f"unsupported character {character!r} at offset {index}")
            index += 1
    result.append(_Token("eof", "", len(text)))
    return result


def _number_value(text: str) -> Decimal:
    """Value of a SPICE number; letters after the scale suffix are units and ignored."""

    if len(text) > _MAX_NUMBER_CHARS:
        raise ExpressionLimitError(
            f"number literal exceeds the {_MAX_NUMBER_CHARS}-character limit"
        )
    match = _NUMBER.fullmatch(text)
    if not match:
        raise ExpressionError(f"invalid number {text!r}")
    exponent = re.search(r"[eE][+-]?([0-9]+)", match.group(1), re.ASCII)
    if exponent is not None and (
        len(exponent.group(1)) > 5 or int(exponent.group(1)) > _MAX_EXPONENT_MAGNITUDE
    ):
        raise ExpressionLimitError("number exponent magnitude exceeds 10000")
    try:
        value = Decimal(match.group(1))
        suffix = match.group(2)
        result = value * _SCALES[suffix.casefold()] if suffix else value
    except InvalidOperation as error:
        raise ExpressionError(f"invalid number {text!r}") from error
    if not result.is_finite():
        raise ExpressionError(f"invalid number {text!r}")
    return result


def parse_number(text: str) -> Decimal | None:
    """Return the value of a plain SPICE number such as ``10pF`` or ``1.8``, else None."""

    try:
        return _number_value(text.strip())
    except ExpressionError:
        return None


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.index = 0
        self.nodes = 0

    def account(self, expression: Expr) -> Expr:
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

    def expect(self, text: str, message: str) -> None:
        if self.current.text != text or self.current.kind == "eof":
            raise ExpressionError(message)
        self.consume()

    def parse(self) -> Expr:
        if self.current.kind == "eof":
            raise ExpressionError("empty expression")
        expression = self.parse_precedence(0, 0)
        if self.current.kind != "eof":
            raise ExpressionError(
                f"unexpected token {self.current.text!r} at offset {self.current.offset}"
            )
        return expression

    def parse_primary(self, depth: int) -> Expr:
        token = self.consume()
        if token.kind == "number":
            return self.account(Number(_number_value(token.text), token.text))
        if token.kind == "name":
            if self.current.text == "(" and self.current.kind == "paren":
                self.consume()
                arguments: list[Expr] = []
                if self.current.text != ")":
                    while True:
                        arguments.append(self.parse_precedence(0, depth + 1))
                        if self.current.text == ",":
                            self.consume()
                            continue
                        break
                self.expect(")", f"missing closing parenthesis in call to {token.text}")
                return self.account(Call(token.text, tuple(arguments)))
            return self.account(Name(token.text))
        if token.kind == "operator" and token.text in {"+", "-", "!"}:
            operand = self.parse_precedence(_UNARY_PRECEDENCE, depth + 1)
            return self.account(Unary(token.text, operand))
        if token.text == "(" and token.kind == "paren":
            inner = self.parse_precedence(0, depth + 1)
            self.expect(")", "missing closing parenthesis")
            return inner
        raise ExpressionError(f"unexpected token {token.text!r} at offset {token.offset}")

    def parse_precedence(self, minimum: int, depth: int) -> Expr:
        if depth > _MAX_EXPRESSION_DEPTH:
            raise ExpressionLimitError(f"expression nesting exceeds {_MAX_EXPRESSION_DEPTH} levels")
        left = self.parse_primary(depth)
        while True:
            token = self.current
            if token.kind == "punct" and token.text == "?" and minimum <= _TERNARY_PRECEDENCE:
                self.consume()
                if_true = self.parse_precedence(0, depth + 1)
                self.expect(":", "ternary expression is missing ':'")
                if_false = self.parse_precedence(_TERNARY_PRECEDENCE, depth + 1)
                left = self.account(Ternary(left, if_true, if_false))
                continue
            if token.kind != "operator" or token.text not in _BINARY_PRECEDENCE:
                break
            level = _BINARY_PRECEDENCE[token.text]
            if level < minimum:
                break
            self.consume()
            right_associative = token.text in {"^", "**"}
            right = self.parse_precedence(level if right_associative else level + 1, depth + 1)
            left = self.account(Binary(token.text, left, right))
        return left


def strip_delimiters(text: str) -> str:
    """Remove one pair of enclosing ``{}`` or quotes, the two ways SPICE marks an expression."""

    stripped = text.strip()
    if (
        len(stripped) >= 2
        and stripped[0] == stripped[-1]
        and stripped[0] in {"'", '"'}
        and stripped[0] not in stripped[1:-1]
    ):
        return stripped[1:-1].strip()
    return stripped


@lru_cache(maxsize=65_536)
def parse_expression(text: str) -> Expr:
    """Parse one SPICE expression, with or without ``{}``/quote delimiters.

    Braces need no special handling: the tokenizer reads them as parentheses.
    Results are cached because model libraries repeat the same values a lot.
    """

    return _Parser(strip_delimiters(text)).parse()


def expression_names(expression: Expr) -> frozenset[str]:
    """Parameter names an expression reads (case-folded); probe arguments are excluded."""

    found: set[str] = set()
    pending: list[Expr] = [expression]
    while pending:
        item = pending.pop()
        if isinstance(item, Name):
            found.add(item.value.casefold())
        elif isinstance(item, Unary):
            pending.append(item.operand)
        elif isinstance(item, Binary):
            pending.extend((item.left, item.right))
        elif isinstance(item, Ternary):
            pending.extend((item.condition, item.if_true, item.if_false))
        elif isinstance(item, Call) and item.name.casefold() not in PROBE_FUNCTIONS:
            pending.extend(item.arguments)
    return frozenset(found)


def expression_calls(expression: Expr) -> frozenset[str]:
    """Function names an expression calls (case-folded)."""

    found: set[str] = set()
    pending: list[Expr] = [expression]
    while pending:
        item = pending.pop()
        if isinstance(item, Unary):
            pending.append(item.operand)
        elif isinstance(item, Binary):
            pending.extend((item.left, item.right))
        elif isinstance(item, Ternary):
            pending.extend((item.condition, item.if_true, item.if_false))
        elif isinstance(item, Call):
            found.add(item.name.casefold())
            if item.name.casefold() not in PROBE_FUNCTIONS:
                pending.extend(item.arguments)
    return frozenset(found)


def _check_exponent(right: Decimal) -> None:
    if abs(right) > _MAX_EXPONENT_MAGNITUDE:
        raise ExpressionError("exponent magnitude exceeds 10000")


def _power(left: Decimal, right: Decimal) -> Decimal:
    _check_exponent(right)
    if right == right.to_integral_value():
        return left ** int(right)
    if left < 0:
        raise ExpressionError("negative base with a non-integer exponent")
    return left**right


def _float_function(function: Callable[..., float]) -> Callable[..., Decimal]:
    def apply(*values: Decimal) -> Decimal:
        try:
            result = function(*(float(value) for value in values))
        except (OverflowError, ValueError) as error:
            raise ExpressionError(str(error)) from error
        if not math.isfinite(result):
            raise ExpressionError("result is not finite")
        return Decimal(repr(result))

    return apply


def _ln(value: Decimal) -> Decimal:
    if value <= 0:
        raise ExpressionError("logarithm of a non-positive number")
    return value.ln()


def _log10(value: Decimal) -> Decimal:
    if value <= 0:
        raise ExpressionError("logarithm of a non-positive number")
    return value.log10()


def _sqrt(value: Decimal) -> Decimal:
    if value < 0:
        raise ExpressionError("square root of a negative number")
    return value.sqrt()


def _exp(value: Decimal) -> Decimal:
    if value > 700:
        raise ExpressionError("exp() overflows")
    return value.exp()


def _pwr(base: Decimal, exponent: Decimal) -> Decimal:
    # ngspice: pwr(x, y) = sign(x) * |x|^y
    magnitude = _power(abs(base), exponent)
    return -magnitude if base < 0 else magnitude


def _sign(value: Decimal) -> Decimal:
    return Decimal(1) if value > 0 else Decimal(-1) if value < 0 else Decimal(0)


def _truncate(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding="ROUND_DOWN")


def _round(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding="ROUND_HALF_UP")


def _floor(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding="ROUND_FLOOR")


def _ceil(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding="ROUND_CEILING")


def _step(value: Decimal) -> Decimal:
    return Decimal(1) if value > 0 else Decimal(0)


def _ramp(value: Decimal) -> Decimal:
    return value if value > 0 else Decimal(0)


def _choose(condition: Decimal, if_true: Decimal, if_false: Decimal) -> Decimal:
    return if_true if condition != 0 else if_false


# name -> (minimum arguments, maximum arguments, implementation)
_BUILTINS: dict[str, tuple[int, int, Callable[..., Decimal]]] = {
    "abs": (1, 1, abs),
    "sqrt": (1, 1, _sqrt),
    "exp": (1, 1, _exp),
    "ln": (1, 1, _ln),
    "log": (1, 1, _ln),
    "log10": (1, 1, _log10),
    "pow": (2, 2, _power),
    "pwr": (2, 2, _pwr),
    "min": (2, 64, min),
    "max": (2, 64, max),
    "sgn": (1, 1, _sign),
    "sign": (1, 1, _sign),
    "int": (1, 1, _truncate),
    "nint": (1, 1, _round),
    "floor": (1, 1, _floor),
    "ceil": (1, 1, _ceil),
    "u": (1, 1, _step),
    "uramp": (1, 1, _ramp),
    "ternary_fcn": (3, 3, _choose),
    "sin": (1, 1, _float_function(math.sin)),
    "cos": (1, 1, _float_function(math.cos)),
    "tan": (1, 1, _float_function(math.tan)),
    "asin": (1, 1, _float_function(math.asin)),
    "acos": (1, 1, _float_function(math.acos)),
    "atan": (1, 1, _float_function(math.atan)),
    "atan2": (2, 2, _float_function(math.atan2)),
    "sinh": (1, 1, _float_function(math.sinh)),
    "cosh": (1, 1, _float_function(math.cosh)),
    "tanh": (1, 1, _float_function(math.tanh)),
    "asinh": (1, 1, _float_function(math.asinh)),
    "acosh": (1, 1, _float_function(math.acosh)),
    "atanh": (1, 1, _float_function(math.atanh)),
}
BUILTIN_FUNCTIONS = frozenset(_BUILTINS)


def _apply_unary(operator: str, value: Decimal) -> Decimal:
    if operator == "-":
        return -value
    if operator == "!":
        return Decimal(0) if value != 0 else Decimal(1)
    return value


def _apply_binary(operator: str, left: Decimal, right: Decimal) -> Decimal:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator == "*":
        return left * right
    if operator == "/":
        if right == 0:
            raise ExpressionError("division by zero")
        return left / right
    if operator in {"^", "**"}:
        return _power(left, right)
    truth = {
        "==": left == right,
        "!=": left != right,
        "<": left < right,
        ">": left > right,
        "<=": left <= right,
        ">=": left >= right,
        "&&": left != 0 and right != 0,
        "||": left != 0 or right != 0,
    }[operator]
    return Decimal(1) if truth else Decimal(0)


def _number(value: Decimal) -> Number:
    if not value.is_finite():
        raise ExpressionError("result is not finite")
    return Number(value, format_decimal(value))


Rename: TypeAlias = Callable[[str], str]


def _keep(name: str) -> str:
    return name


def fold_expression(
    expression: Expr,
    environment: Mapping[str, Expr | Decimal],
    *,
    functions: Mapping[str, FunctionDef] | None = None,
    node: Rename = _keep,
    device: Rename = _keep,
) -> Expr:
    """Substitute known parameters and fold constants, keeping simulator-time terms.

    ``environment`` maps case-folded names to numbers or to already-folded
    expressions.  ``node`` and ``device`` rename the arguments of ``v()`` and
    ``i()``, which is what flattening needs.  Undefined names, unknown functions
    and arithmetic faults raise :class:`ExpressionError`.
    """

    folder = _Folder(functions or {}, node, device)
    try:
        with localcontext() as context:
            context.prec = 28
            return folder.fold(expression, environment, 0)
    except ArithmeticError as error:
        raise ExpressionError(f"cannot evaluate expression: {error}") from error
    except RecursionError as error:
        raise ExpressionLimitError("expression nests too deeply to evaluate") from error


class _Folder:
    def __init__(self, functions: Mapping[str, FunctionDef], node: Rename, device: Rename) -> None:
        self.functions = functions
        self.node = node
        self.device = device

    def fold(self, expression: Expr, environment: Mapping[str, Expr | Decimal], depth: int) -> Expr:
        if isinstance(expression, Number):
            return expression
        if isinstance(expression, Name):
            key = expression.value.casefold()
            if key in environment:
                bound = environment[key]
                return _number(bound) if isinstance(bound, Decimal) else bound
            if key in RUNTIME_NAMES:
                return Name(key)
            raise ExpressionError(f"undefined parameter {expression.value!r}")
        if isinstance(expression, Unary):
            operand = self.fold(expression.operand, environment, depth)
            if isinstance(operand, Number):
                return _number(_apply_unary(expression.operator, operand.value))
            return Unary(expression.operator, operand)
        if isinstance(expression, Binary):
            left = self.fold(expression.left, environment, depth)
            right = self.fold(expression.right, environment, depth)
            if isinstance(left, Number) and isinstance(right, Number):
                return _number(_apply_binary(expression.operator, left.value, right.value))
            return Binary(expression.operator, left, right)
        if isinstance(expression, Ternary):
            condition = self.fold(expression.condition, environment, depth)
            if isinstance(condition, Number):
                chosen = expression.if_true if condition.value != 0 else expression.if_false
                return self.fold(chosen, environment, depth)
            return Ternary(
                condition,
                self.fold(expression.if_true, environment, depth),
                self.fold(expression.if_false, environment, depth),
            )
        return self.fold_call(expression, environment, depth)

    def fold_call(self, call: Call, environment: Mapping[str, Expr | Decimal], depth: int) -> Expr:
        key = call.name.casefold()
        if key in PROBE_FUNCTIONS:
            rename = self.node if key == "v" else self.device
            arguments: list[Expr] = []
            for argument in call.arguments:
                if isinstance(argument, Name):
                    arguments.append(Name(rename(argument.value)))
                elif isinstance(argument, Number):
                    arguments.append(Name(rename(argument.original)))
                else:
                    raise ExpressionError(f"{call.name}() takes circuit names")
            if not 1 <= len(arguments) <= (2 if key == "v" else 1):
                raise ExpressionError(f"{call.name}() has the wrong number of arguments")
            return Call(call.name, tuple(arguments))
        folded = tuple(self.fold(argument, environment, depth) for argument in call.arguments)
        if key in self.functions:
            if depth >= _MAX_FUNCTION_DEPTH:
                raise ExpressionLimitError("user function calls nest too deeply")
            definition = self.functions[key]
            if len(folded) != len(definition.parameters):
                raise ExpressionError(
                    f"{definition.name}() takes {len(definition.parameters)} argument(s)"
                )
            scope: dict[str, Expr | Decimal] = dict(environment)
            scope.update(
                (parameter.casefold(), value)
                for parameter, value in zip(definition.parameters, folded, strict=True)
            )
            return self.fold(definition.body, scope, depth + 1)
        if key in STATISTICAL_FUNCTIONS:
            return Call(call.name, folded)
        builtin = _BUILTINS.get(key)
        if builtin is None:
            raise ExpressionError(f"unknown function {call.name!r}")
        low, high, implementation = builtin
        if not low <= len(folded) <= high:
            raise ExpressionError(f"{call.name}() has the wrong number of arguments")
        if all(isinstance(item, Number) for item in folded):
            values = [item.value for item in folded if isinstance(item, Number)]
            return _number(implementation(*values))
        return Call(call.name, folded)


def evaluate_expression(
    expression: Expr,
    environment: Mapping[str, Decimal],
    *,
    functions: Mapping[str, FunctionDef] | None = None,
) -> Decimal:
    """Evaluate an expression to a number, or raise if it depends on the simulation."""

    folded = fold_expression(expression, environment, functions=functions)
    if not isinstance(folded, Number):
        raise ExpressionError(
            "value depends on a simulation-time quantity: " + format_expression(folded)
        )
    return folded.value


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
    """Canonical spelling: every binary or ternary operation sits in its own braces.

    This is the form stored in circuit IR.  It is unambiguous and parses back to
    the same tree, at the cost of looking unusual.  Use :func:`spice_expression`
    when writing a deck.
    """

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
        elif isinstance(item, Binary):
            pending.extend(("}", item.right, f" {item.operator} ", item.left, "{"))
        elif isinstance(item, Ternary):
            pending.extend(("}", item.if_false, " : ", item.if_true, " ? ", item.condition, "{"))
        else:
            pending.append(")")
            for position, argument in enumerate(reversed(item.arguments)):
                if position:
                    pending.append(", ")
                pending.append(argument)
            pending.append(f"{item.name}(")
    return "".join(rendered)


def _infix(expression: Expr, parent: int = 0) -> str:
    if isinstance(expression, Number):
        return expression.original
    if isinstance(expression, Name):
        return expression.value
    if isinstance(expression, Call):
        return f"{expression.name}({','.join(_infix(item) for item in expression.arguments)})"
    if isinstance(expression, Unary):
        text = f"{expression.operator}{_infix(expression.operand, _UNARY_PRECEDENCE + 1)}"
        return f"({text})" if parent > _UNARY_PRECEDENCE else text
    if isinstance(expression, Ternary):
        text = (
            f"{_infix(expression.condition, _TERNARY_PRECEDENCE + 1)} ? "
            f"{_infix(expression.if_true)} : {_infix(expression.if_false, _TERNARY_PRECEDENCE)}"
        )
        return f"({text})" if parent > _TERNARY_PRECEDENCE else text
    level = _BINARY_PRECEDENCE[expression.operator]
    if expression.operator in {"^", "**"}:
        left_level, right_level = level + 1, level
    else:
        left_level, right_level = level, level + 1
    text = (
        f"{_infix(expression.left, left_level)}{expression.operator}"
        f"{_infix(expression.right, right_level)}"
    )
    return f"({text})" if parent > level else text


def spice_expression(expression: Expr) -> str:
    """Spelling for a SPICE deck: a bare number, or ``{...}`` around anything else."""

    if isinstance(expression, Number):
        return expression.original
    return "{" + _infix(expression) + "}"
