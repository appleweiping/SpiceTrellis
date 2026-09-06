"""Selecting one named section of a library file, which is how corners are chosen."""

from pathlib import Path

import pytest

from spicetrellis.api import analyze_file, inventory
from spicetrellis.emit import format_statement
from spicetrellis.model import LibCall, LibSectionEnd, LibSectionStart
from spicetrellis.parser import parse_text

CORNERS = """* Clean-room placeholder corner declarations; not simulation models.
.lib tt
.model nch nmos level=1 vto=0.50
.endl tt

.lib ff
.model nch nmos level=1 vto=0.42
.endl ff

.lib ss
.model nch nmos level=1 vto=0.58
.endl ss
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(tmp_path: Path, deck: str, library: str = CORNERS) -> Path:
    _write(tmp_path / "corners.lib", library)
    return _write(tmp_path / "top.sp", deck)


def _codes(analysis) -> set[str]:
    return {item.code for item in analysis.diagnostics}


def _models(analysis) -> list[str]:
    """Model cards that survived expansion, which is what selection decides.

    ``deck.files`` holds every file that was parsed, including sections that
    were never called, so it answers a different question.
    """

    return [
        format_statement(statement)
        for statement in analysis.deck.top
        if format_statement(statement).startswith(".model")
    ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_a_call_names_a_file_and_a_section():
    statement = parse_text('.lib "corners.lib" tt\n', "top.sp").statements[0]
    assert isinstance(statement, LibCall)
    assert statement.target == "corners.lib"
    assert statement.section == "tt"


def test_a_single_argument_opens_a_section():
    statement = parse_text(".lib tt\n", "corners.lib").statements[0]
    assert isinstance(statement, LibSectionStart)
    assert statement.name == "tt"


@pytest.mark.parametrize(
    ("text", "expected"),
    [(".endl\n", None), (".endl tt\n", "tt")],
)
def test_endl_optionally_names_its_section(text, expected):
    statement = parse_text(text, "corners.lib").statements[0]
    assert isinstance(statement, LibSectionEnd)
    assert statement.name == expected


@pytest.mark.parametrize(
    "text",
    ['.lib "a.lib" tt extra\n', ".endl a b\n"],
)
def test_malformed_library_cards_are_refused(text):
    assert any(item.code.startswith("ST1") for item in parse_text(text, "x.sp").diagnostics)


@pytest.mark.parametrize(
    ("text", "rendered"),
    [
        ('.lib "corners.lib" tt\n', ".lib corners.lib tt"),
        (".lib corners.lib tt\n", ".lib corners.lib tt"),
        (".lib tt\n", ".lib tt"),
        (".endl\n", ".endl"),
        (".endl tt\n", ".endl tt"),
        ('.lib "with space.lib" tt\n', '.lib "with space.lib" tt'),
    ],
)
def test_library_cards_round_trip_through_the_emitter(text, rendered):
    # A path is quoted only when it needs to be, so an unnecessary pair of
    # quotes does not survive a round trip.
    statement = parse_text(text, "x.sp").statements[0]
    assert format_statement(statement) == rendered


# ---------------------------------------------------------------------------
# Selecting a corner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("section", "threshold"), [("tt", "0.50"), ("ff", "0.42"), ("ss", "0.58")])
def test_only_the_named_section_is_inlined(tmp_path, section, threshold):
    deck = _project(tmp_path, f'* deck\n.lib "corners.lib" {section}\nR1 a 0 1k\n.end\n')
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    models = _models(analysis)
    assert len(models) == 1
    assert threshold in models[0]


def test_a_section_name_is_matched_without_case(tmp_path):
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" TT\nR1 a 0 1k\n.end\n')
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    assert "0.50" in _models(analysis)[0]


def test_one_file_may_supply_two_different_sections(tmp_path):
    # Two corners in one deck is unusual but well defined, and it must not look
    # like a cycle just because the same file is opened twice.
    deck = _project(
        tmp_path,
        '* deck\n.lib "corners.lib" tt\n.lib "corners.lib" ff\nR1 a 0 1k\n.end\n',
    )
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    assert len(_models(analysis)) == 2


def test_a_section_may_include_another_file(tmp_path):
    _write(tmp_path / "shared.sp", ".model pch pmos level=1 vto=-0.5\n")
    library = ".lib tt\n.include shared.sp\n.model nch nmos level=1 vto=0.5\n.endl\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\nR1 a 0 1k\n.end\n', library)
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    assert len(_models(analysis)) == 2


def test_a_section_may_call_another_section(tmp_path):
    library = (
        ".lib base\n.model nch nmos level=1\n.endl\n"
        '.lib tt\n.lib "corners.lib" base\n.model pch pmos level=1\n.endl\n'
    )
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\nR1 a 0 1k\n.end\n', library)
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    assert len(_models(analysis)) == 2


def test_an_uncalled_section_contributes_nothing(tmp_path):
    # Including a library wholesale must not quietly merge every corner at once,
    # which is the whole reason for selecting one by name.
    deck = _project(tmp_path, "* deck\n.include corners.lib\nR1 a 0 1k\n.end\n")
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    assert _models(analysis) == []


def test_calls_are_counted_in_the_inventory(tmp_path):
    deck = _project(
        tmp_path,
        '* deck\n.lib "corners.lib" tt\n.lib "corners.lib" ff\nR1 a 0 1k\n.end\n',
    )
    result = inventory(analyze_file(deck)).as_dict()
    assert result["library_sections"] == 2
    assert result["includes"] == 0


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_an_undefined_section_is_reported_with_the_available_ones(tmp_path):
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" fs\nR1 a 0 1k\n.end\n')
    analysis = analyze_file(deck)
    assert "ST2011" in _codes(analysis)
    message = next(item.message for item in analysis.diagnostics if item.code == "ST2011")
    assert "ff, ss, tt" in message


def test_a_duplicated_section_is_reported(tmp_path):
    library = ".lib tt\n.model a nmos level=1\n.endl\n.lib tt\n.model b nmos level=1\n.endl\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\n.end\n', library)
    assert "ST2012" in _codes(analyze_file(deck))


def test_a_stray_endl_is_reported(tmp_path):
    library = ".endl\n.lib tt\n.model a nmos level=1\n.endl\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\n.end\n', library)
    assert "ST2013" in _codes(analyze_file(deck))


def test_an_endl_naming_a_different_section_is_reported(tmp_path):
    library = ".lib tt\n.model a nmos level=1\n.endl ff\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\n.end\n', library)
    assert "ST2013" in _codes(analyze_file(deck))


def test_an_unterminated_section_is_reported(tmp_path):
    library = ".lib tt\n.model a nmos level=1\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" tt\n.end\n', library)
    assert "ST2014" in _codes(analyze_file(deck))


def test_a_deck_that_opens_a_section_by_accident_is_told_so(tmp_path):
    # The one-argument `.lib FILE` form of another dialect parses here as an
    # unterminated section. Without this diagnostic it would silently discard
    # every card after it.
    deck = _project(tmp_path, "* deck\n.lib corners.lib\nR1 a 0 1k\n.end\n")
    analysis = analyze_file(deck)
    assert "ST2014" in _codes(analysis)


def test_nested_sections_are_reported(tmp_path):
    library = ".lib outer\n.lib inner\n.model a nmos level=1\n.endl\n.endl\n"
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" outer\n.end\n', library)
    assert "ST2015" in _codes(analyze_file(deck))


def test_a_section_that_calls_itself_is_reported(tmp_path):
    library = '.lib loop\n.lib "corners.lib" loop\n.endl\n'
    deck = _project(tmp_path, '* deck\n.lib "corners.lib" loop\n.end\n', library)
    analysis = analyze_file(deck)
    assert "ST2016" in _codes(analysis)


def test_a_library_outside_the_allowed_roots_is_refused(tmp_path):
    outside = tmp_path.parent / "outside.lib"
    _write(outside, CORNERS)
    deck = _write(tmp_path / "top.sp", '* deck\n.lib "../outside.lib" tt\n.end\n')
    analysis = analyze_file(deck, include_roots=(tmp_path,))
    assert "ST2004" in _codes(analysis)
    message = next(item.message for item in analysis.diagnostics if item.code == "ST2004")
    assert message.startswith("library path")


def test_a_missing_library_file_is_reported(tmp_path):
    deck = _write(tmp_path / "top.sp", '* deck\n.lib "absent.lib" tt\n.end\n')
    analysis = analyze_file(deck)
    assert analysis.has_errors
