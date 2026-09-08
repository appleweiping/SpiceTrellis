import pytest

from spicetrellis.model import (
    Blank,
    Comment,
    Element,
    Global,
    Include,
    Model,
    Opaque,
    Param,
    SubcktEnd,
    SubcktStart,
)
from spicetrellis.parser import CardParseError, parse_text, split_fields
from spicetrellis.source import logical_cards, split_inline_comment


def test_deep_expression_is_a_diagnostic_instead_of_recursion_failure():
    text = ".param x=" + "(" * 2_000 + "1" + ")" * 2_000
    deck = parse_text(text)
    assert {item.code for item in deck.diagnostics} == {"ST1004"}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("1e9999999999999999999", "exponent magnitude exceeds 10000"),
        ("9" * 129, "128-character limit"),
    ],
)
def test_expression_limit_diagnostics_preserve_the_actual_cause(value: str, message: str) -> None:
    deck = parse_text(f"R1 a 0 {value}\n")
    assert [item.code for item in deck.diagnostics] == ["ST1004"]
    assert message in deck.diagnostics[0].message
    assert "nesting" not in deck.diagnostics[0].message


def test_flat_expression_over_token_budget_is_a_bounded_diagnostic() -> None:
    deck = parse_text("R1 a 0 " + "+".join(["1"] * 2_000) + "\n")
    assert [item.code for item in deck.diagnostics] == ["ST1004"]
    assert "1024-token limit" in deck.diagnostics[0].message


def test_logical_continuation_retains_physical_segments():
    cards, diagnostics = logical_cards("R1 a b {base\n+ * scale} $ note\n", "deck.sp")
    assert diagnostics == []
    assert cards[0].code == "R1 a b {base * scale}"
    assert cards[0].inline_comment == "note"
    assert len(cards[0].segments) == 2
    assert cards[0].span.start_line == 1
    assert cards[0].span.end_line == 2


def test_source_range_is_one_based_and_half_open() -> None:
    cards, diagnostics = logical_cards("R1 in 0 1k\n", "deck.sp")
    assert diagnostics == []
    assert cards[0].span.as_dict() == {
        "file": "deck.sp",
        "start": [1, 1],
        "end": [1, 11],
    }


def test_source_columns_count_unicode_scalars_instead_of_utf8_bytes() -> None:
    text = "R1 in 0 1k ; café😀"
    cards, diagnostics = logical_cards(text + "\n", "unicode.sp")
    assert diagnostics == []
    assert cards[0].span.end_col == len(text) + 1
    assert cards[0].span.end_col != len(text.encode("utf-8")) + 1


def test_orphan_continuation_is_reported_and_preserved():
    deck = parse_text("+ R1 a b 1k\n")
    assert deck.diagnostics[0].code == "ST1001"
    assert isinstance(deck.statements[0], Opaque)


def test_comments_inside_quotes_and_braces_are_not_split():
    code, comment, marker = split_inline_comment('V1 a 0 "a;b" {x$y} ; actual')
    assert code == 'V1 a 0 "a;b" {x$y}'
    assert comment == "actual"
    assert marker == ";"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('a "b c" {d + e}', ["a", '"b c"', "{d + e}"]),
        ("a (b c) d", ["a", "(b c)", "d"]),
        ("x=1 y = {x * 2}", ["x=1", "y", "=", "{x * 2}"]),
    ],
)
def test_split_fields_respects_grouping(source, expected):
    assert split_fields(source) == expected


@pytest.mark.parametrize("source", ['a "unterminated', "a {b", "a b)"])
def test_split_fields_rejects_unbalanced_input(source):
    with pytest.raises(CardParseError):
        split_fields(source)


def test_parser_covers_supported_statement_families():
    deck = parse_text(
        """* title

.include "models/basic models.sp"
.param width=2u length = 180n
.global 0 vdd
.model nch nmos level=1
.subckt gain in out SCALE=2
R1 in mid {1k*SCALE}
C1 mid 0 2p
L1 mid out 1n
V1 out 0 DC 1
I1 in 0 2u
M1 out in 0 0 nch W={width} L={length}
Xnested in out other ratio=2
.ends gain
.end
""",
        "complete.sp",
    )
    assert not [item for item in deck.diagnostics if item.severity == "error"]
    assert isinstance(deck.statements[0], Comment)
    assert isinstance(deck.statements[1], Blank)
    assert isinstance(deck.statements[2], Include)
    assert isinstance(deck.statements[3], Param)
    assert isinstance(deck.statements[4], Global)
    assert isinstance(deck.statements[5], Model)
    assert isinstance(deck.statements[6], SubcktStart)
    assert [item.family for item in deck.statements if isinstance(item, Element)] == [
        "R",
        "C",
        "L",
        "V",
        "I",
        "M",
        "X",
    ]
    assert isinstance(deck.statements[-2], SubcktEnd)


def test_unknown_cards_are_preserved_with_warnings():
    deck = parse_text("D1 a 0 diode\n.option post\n")
    assert all(isinstance(statement, Opaque) for statement in deck.statements)
    assert [item.code for item in deck.diagnostics] == ["ST1101", "ST1101"]


@pytest.mark.parametrize(
    "source",
    [
        ".include one two",
        ".param",
        ".subckt",
        ".ends a b",
        ".model only_name",
        ".global",
        ".end extra",
        "Rbad only_one 1k",
        "Mbad d g s model",
        "Xbad only_subckt",
    ],
)
def test_invalid_cards_produce_recoverable_diagnostics(source):
    deck = parse_text(source + "\n")
    assert deck.diagnostics[0].code == "ST1002"
    assert isinstance(deck.statements[0], Opaque)


def test_end_is_terminal_and_later_statements_are_not_parsed():
    deck = parse_text("R1 a 0 1k\n.end\nR2 b 0 2k\n* trailing comment\n", "terminal.sp")
    assert [item.code for item in deck.diagnostics] == ["ST1003"]
    elements = [item for item in deck.statements if isinstance(item, Element)]
    assert [item.name for item in elements] == ["R1"]
