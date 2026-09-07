"""Asking the source map questions, in both directions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spicetrellis.api import analyze_file, flatten
from spicetrellis.cli import main
from spicetrellis.model import Provenance, SourceSpan
from spicetrellis.provenance import (
    PATH_SEPARATOR,
    Origin,
    ProvenanceIndex,
    build_index,
    index_from_entries,
)

EXAMPLE = Path(__file__).parents[1] / "examples" / "hierarchical_filter"
TOP = EXAMPLE / "top.sp"
CELLS = EXAMPLE / "cells" / "passive stages.sp"


@pytest.fixture(scope="module")
def index() -> ProvenanceIndex:
    result = analyze_file(str(TOP), include_roots=(str(EXAMPLE),))
    assert not result.has_errors
    deck = flatten(result)
    assert not deck.has_errors
    return build_index(deck)


def span(name: str, line: int) -> SourceSpan:
    return SourceSpan(name, line, 1, line, 10)


# ---------------------------------------------------------------------------
# Backwards: a card to its source.
# ---------------------------------------------------------------------------


def test_a_nested_card_reports_its_whole_expansion_chain(index: ProvenanceIndex) -> None:
    """The definition alone does not say which copy failed."""

    origin = index.by_name("Xfilter__Xsecond__Cshunt")
    assert origin is not None
    assert origin.depth == 2
    assert origin.definition.filename.endswith("passive stages.sp")
    assert [item.start_line for item in origin.expansion_chain] == [5, 10]


def test_two_copies_share_a_definition_and_differ_in_chain(
    index: ProvenanceIndex,
) -> None:
    first = index.by_name("Xfilter__Xfirst__Rseries")
    second = index.by_name("Xfilter__Xsecond__Rseries")
    assert first is not None and second is not None
    assert first.definition == second.definition
    assert first.expansion_chain != second.expansion_chain


def test_a_top_level_card_has_no_chain(index: ProvenanceIndex) -> None:
    origin = index.by_name("Vinput")
    assert origin is not None
    assert origin.depth == 0
    assert origin.instance_path == ()
    assert origin.local_name == "Vinput"


def test_the_instance_path_splits_on_the_elaborator_separator(
    index: ProvenanceIndex,
) -> None:
    """The elaborator encodes segments so `__` cannot occur inside one."""

    assert PATH_SEPARATOR == "__"
    origin = index.by_name("Xfilter__Xfirst__Cshunt")
    assert origin is not None
    assert origin.instance_path == ("Xfilter", "Xfirst")
    assert origin.local_name == "Cshunt"


def test_a_name_containing_an_encoded_underscore_still_splits() -> None:
    # `_u` is how the elaborator writes a literal underscore, so a name with
    # one must not be mistaken for a path separator.
    origin = Origin(0, "Xa_ub__R_uname", span("t.sp", 1), ())
    assert origin.instance_path == ("Xa_ub",)
    assert origin.local_name == "R_uname"


def test_an_unknown_card_is_absent_rather_than_guessed(index: ProvenanceIndex) -> None:
    assert index.by_name("nothing-like-this") is None
    assert index.by_index(10_000) is None


def test_a_card_describes_itself_outermost_site_first(index: ProvenanceIndex) -> None:
    origin = index.by_name("Xfilter__Xsecond__Rseries")
    assert origin is not None
    lines = origin.describe().splitlines()
    assert lines[0].startswith("Xfilter__Xsecond__Rseries")
    assert "top.sp" in lines[1]
    assert lines[-1].lstrip().startswith("defined at")
    indents = [len(line) - len(line.lstrip()) for line in lines[1:]]
    assert indents == sorted(indents)


# ---------------------------------------------------------------------------
# Forwards: a source line to its cards.
# ---------------------------------------------------------------------------


def test_one_line_in_a_twice_used_subcircuit_becomes_two_cards(
    index: ProvenanceIndex,
) -> None:
    use = index.by_source(str(CELLS.resolve()), 3)
    assert use.copies == 2
    assert [origin.output_name for origin in use.origins] == [
        "Xfilter__Xfirst__Rseries",
        "Xfilter__Xsecond__Rseries",
    ]


def test_cards_come_back_in_output_order(index: ProvenanceIndex) -> None:
    use = index.by_source(str(CELLS.resolve()), 3)
    indices = [origin.output_index for origin in use.origins]
    assert indices == sorted(indices)


def test_a_line_that_produces_nothing_is_a_real_answer(index: ProvenanceIndex) -> None:
    """Not an error: a line nothing instantiates produces no card."""

    use = index.by_source(str(CELLS.resolve()), 9999)
    assert use.copies == 0
    assert use.origins == ()


def test_an_unknown_file_produces_nothing(index: ProvenanceIndex) -> None:
    assert index.by_source("no-such-file.sp", 1).copies == 0


def test_every_line_of_a_continued_statement_finds_the_card() -> None:
    # A reader pointing at any physical line of a continued statement means the
    # same statement.
    entry = Provenance(0, "R1", SourceSpan("t.sp", 4, 1, 6, 12), ())
    index = ProvenanceIndex([entry])
    assert index.by_source("t.sp", 4).copies == 1
    assert index.by_source("t.sp", 5).copies == 1
    assert index.by_source("t.sp", 6).copies == 1
    assert index.by_source("t.sp", 7).copies == 0


# ---------------------------------------------------------------------------
# Subtrees.
# ---------------------------------------------------------------------------


def test_an_instance_reports_everything_beneath_it(index: ProvenanceIndex) -> None:
    assert len(index.under(("Xfilter",))) == 4
    assert len(index.under(("Xfilter", "Xfirst"))) == 2


def test_an_empty_path_is_the_whole_deck(index: ProvenanceIndex) -> None:
    assert len(index.under(())) == len(index.origins)


def test_an_unknown_instance_is_empty(index: ProvenanceIndex) -> None:
    assert index.under(("Xnothing",)) == ()


def test_a_prefix_matches_on_segments_not_characters(index: ProvenanceIndex) -> None:
    # `Xfilt` is a prefix of the string `Xfilter` but not of the path.
    assert index.under(("Xfilt",)) == ()


# ---------------------------------------------------------------------------
# Resolving a query.
# ---------------------------------------------------------------------------


def test_a_name_is_tried_before_an_index(index: ProvenanceIndex) -> None:
    """A deck may legitimately contain a card named with digits."""

    entries = [
        Provenance(0, "12", span("t.sp", 1), ()),
        Provenance(12, "R1", span("t.sp", 2), ()),
    ]
    small = ProvenanceIndex(entries)
    found = small.resolve("12")
    assert found is not None
    assert found.output_name == "12"
    assert found.output_index == 0


def test_an_index_resolves_when_no_name_matches(index: ProvenanceIndex) -> None:
    first = index.origins[0]
    found = index.resolve(str(first.output_index))
    assert found is not None
    assert found.output_name == first.output_name


def test_an_unresolvable_query_is_none(index: ProvenanceIndex) -> None:
    assert index.resolve("definitely-not-here") is None
    assert index.resolve("99999") is None


# ---------------------------------------------------------------------------
# Reading a map back.
# ---------------------------------------------------------------------------


def test_a_map_read_back_answers_the_same_questions(index: ProvenanceIndex) -> None:
    """A written map must be as useful as one still in memory."""

    payload = json.loads(json.dumps([origin.as_dict() for origin in index.origins]))
    rebuilt = index_from_entries(payload)
    assert len(rebuilt.origins) == len(index.origins)
    for before, after in zip(index.origins, rebuilt.origins, strict=True):
        assert after.output_name == before.output_name
        assert after.definition == before.definition
        assert after.expansion_chain == before.expansion_chain


@pytest.mark.parametrize(
    "entry",
    [
        "not an object",
        {"output_index": "0", "output_name": "R1", "definition": {}},
        {"output_index": True, "output_name": "R1", "definition": {}},
        {"output_index": 0, "output_name": 5, "definition": {}},
        {"output_index": 0, "output_name": "R1", "definition": "nope"},
        {"output_index": 0, "output_name": "R1", "definition": {"file": 1}},
        {"output_index": 0, "output_name": "R1", "definition": {"file": "t", "start": [1]}},
        {
            "output_index": 0,
            "output_name": "R1",
            "definition": {"file": "t", "start": [1, 1], "end": [1, 2]},
            "expansion_chain": "nope",
        },
    ],
)
def test_a_malformed_map_entry_is_refused(entry: object) -> None:
    with pytest.raises(ValueError):
        index_from_entries([entry])


# ---------------------------------------------------------------------------
# The command.
# ---------------------------------------------------------------------------


def test_the_command_traces_a_card_backwards(capsys) -> None:
    assert (
        main(
            [
                "locate",
                str(TOP),
                "Xfilter__Xsecond__Cshunt",
                "--include-root",
                str(EXAMPLE),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "expanded at" in printed
    assert printed.count("expanded at") == 2
    assert "defined at" in printed


def test_the_command_traces_a_source_line_forwards(capsys) -> None:
    assert (
        main(
            [
                "locate",
                str(TOP),
                "--source",
                f"{CELLS}:3",
                "--include-root",
                str(EXAMPLE),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "produced 2 card(s)" in printed


def test_the_command_reports_a_line_that_produced_nothing(capsys) -> None:
    assert (
        main(["locate", str(TOP), "--source", f"{CELLS}:9999", "--include-root", str(EXAMPLE)]) == 0
    )
    assert "produced no card" in capsys.readouterr().out


def test_the_command_lists_a_subtree(capsys) -> None:
    assert main(["locate", str(TOP), "--under", "Xfilter", "--include-root", str(EXAMPLE)]) == 0
    assert "expanded to 4 card(s)" in capsys.readouterr().out


def test_the_command_emits_json_on_request(tmp_path: Path) -> None:
    target = tmp_path / "origin.json"
    assert (
        main(
            [
                "locate",
                str(TOP),
                "Vinput",
                "--include-root",
                str(EXAMPLE),
                "--json",
                "-o",
                str(target),
            ]
        )
        == 0
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["output_name"] == "Vinput"
    assert payload["instance_path"] == []


def test_the_command_rejects_a_malformed_source_reference(capsys) -> None:
    assert (
        main(["locate", str(TOP), "--source", "no-colon-here", "--include-root", str(EXAMPLE)]) == 2
    )
    assert "FILE:LINE" in capsys.readouterr().err


def test_the_command_reports_an_unknown_card(capsys) -> None:
    assert main(["locate", str(TOP), "nope", "--include-root", str(EXAMPLE)]) == 1
    assert "no flattened card" in capsys.readouterr().err
