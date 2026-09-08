from decimal import Decimal

import pytest

from spicetrellis.expressions import (
    ExpressionError,
    evaluate_expression,
    expression_names,
    format_decimal,
    format_expression,
    parse_expression,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1k", Decimal("1000")),
        ("2.5meg", Decimal("2.5e6")),
        ("3m", Decimal("0.003")),
        ("4u", Decimal("4e-6")),
        ("10mil", Decimal("0.000254")),
        ("{2 + 3 * 4}", Decimal("14")),
        ("2^3^2", Decimal("512")),
        ("-2 + 5", Decimal("3")),
        ("-2^2", Decimal("-4")),
        ("(-2)^2", Decimal("4")),
        ("2^-2", Decimal("0.25")),
    ],
)
def test_expression_evaluation(source, expected):
    assert evaluate_expression(parse_expression(source), {}) == expected


def test_names_are_case_insensitive_and_discoverable():
    expression = parse_expression("{Base * Scale + offset}")
    assert expression_names(expression) == frozenset({"base", "scale", "offset"})
    assert evaluate_expression(
        expression,
        {"base": Decimal("2"), "scale": Decimal("3"), "offset": Decimal("1")},
    ) == Decimal("7")


@pytest.mark.parametrize(
    "source",
    ["", "{1 + }", "sqrt(2)", "1 && 2", "(1 + 2", "1 / 0", "2 ^ 0.5"],
)
def test_invalid_or_unsafe_expressions_are_rejected(source):
    with pytest.raises(ExpressionError):
        expression = parse_expression(source)
        evaluate_expression(expression, {})


def test_undefined_parameter_is_an_explicit_error():
    with pytest.raises(ExpressionError, match="undefined parameter"):
        evaluate_expression(parse_expression("missing + 1"), {})


def test_exponent_magnitude_is_bounded():
    with pytest.raises(ExpressionError, match="magnitude"):
        evaluate_expression(parse_expression("2^10001"), {})


def test_expression_nesting_is_bounded_deterministically():
    source = "(" * 258 + "1" + ")" * 258
    with pytest.raises(ExpressionError, match="nesting exceeds 256 levels"):
        parse_expression(source)


def test_expression_ast_size_is_bounded_independently_of_nesting():
    source = "+".join(["1"] * 257)
    with pytest.raises(ExpressionError, match="512-node limit"):
        parse_expression(source)


@pytest.mark.parametrize(
    "source",
    [
        "\u0661",
        "\uff11",
        "1\u00a0+ 2",
        "1\u212a",
        "1e9999999999999999999",
        "1" * 129,
    ],
)
def test_expression_wire_subset_rejects_cross_language_ambiguity(source: str) -> None:
    with pytest.raises(ExpressionError):
        parse_expression(source)


def test_formatters_are_deterministic():
    expression = parse_expression("{left+2*right}")
    rendered = format_expression(expression)
    assert rendered == "{left + {2 * right}}"
    assert parse_expression(rendered) == expression
    assert format_decimal(Decimal("1.2300")) == "1.23"
    assert format_decimal(Decimal("0")) == "0"
