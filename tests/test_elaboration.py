from pathlib import Path

import pytest

from spicetrellis.api import ElaborationLimits, analyze_file, flatten, format_deck
from spicetrellis.model import Element

FIXTURES = Path(__file__).parent / "fixtures"


def test_hierarchy_is_flattened_with_values_and_provenance():
    analysis = analyze_file(FIXTURES / "valid" / "top.sp")
    result = flatten(analysis)
    assert not result.has_errors
    elements = [item for item in result.statements if isinstance(item, Element)]
    assert all(item.family != "X" for item in elements)
    assert {item.name for item in elements} == {"VDD", "X1__Rup", "X1__Rdown", "Cload"}
    assert {item.nodes for item in elements if item.name.startswith("X1__")} == {
        ("vin", "vout"),
        ("vout", "0"),
    }
    rendered = format_deck(result.statements)
    assert "X1__Rup vin vout 10000" in rendered
    assert "VDD vdd 0 DC 1.2" in rendered
    assert len(result.provenance) == 4
    assert (
        len(
            next(
                item for item in result.provenance if item.output_name == "X1__Rup"
            ).expansion_chain
        )
        == 1
    )


def test_nested_example_expands_to_unique_hierarchical_names():
    root = Path(__file__).parents[1] / "examples" / "hierarchical_filter" / "top.sp"
    result = flatten(analyze_file(root))
    names = [statement.name for statement in result.statements if isinstance(statement, Element)]
    assert "Xfilter__Xfirst__Rseries" in names
    assert "Xfilter__Xsecond__Cshunt" in names
    assert len(names) == len(set(name.casefold() for name in names))


def test_analysis_errors_prevent_partial_flattening():
    analysis = analyze_file(FIXTURES / "invalid" / "semantic.sp")
    result = flatten(analysis)
    assert result.statements == ()
    assert result.has_errors


def test_empty_deck_can_be_flattened(tmp_path):
    path = tmp_path / "empty.sp"
    path.write_text("", encoding="utf-8")
    result = flatten(analyze_file(path))
    assert result.statements == ()
    assert not result.has_errors


def test_hierarchical_name_encoding_is_injective_for_delimiter_like_names(tmp_path):
    path = tmp_path / "names.sp"
    path.write_text(
        ".subckt leaf p n\nRpart p n 1k\n.ends\n"
        ".subckt outer p n\nXb p n leaf\n.ends\n"
        "Xa a 0 outer\n"
        "Xa__Xb b 0 leaf\n"
        ".end\n",
        encoding="utf-8",
    )
    result = flatten(analyze_file(path))
    assert not result.has_errors
    names = [item.name for item in result.statements if isinstance(item, Element)]
    assert names == ["Xa__Xb__Rpart", "Xa_u_uXb__Rpart"]
    assert len(names) == len(set(name.casefold() for name in names))


def test_forward_local_parameters_are_resolved_for_earlier_elements(tmp_path):
    path = tmp_path / "forward-param.sp"
    path.write_text(
        ".subckt cell p n\n"
        "R1 p n later\n"
        ".param later=base*2\n"
        ".param base=1k\n"
        ".ends\n"
        "X1 a 0 cell\n"
        ".end\n",
        encoding="utf-8",
    )
    analysis = analyze_file(path)
    assert not analysis.has_errors
    result = flatten(analysis)
    assert not result.has_errors
    assert "X1__R1 a 0 2000" in format_deck(result.statements)


def test_long_reverse_parameter_chain_uses_dependency_scheduling(tmp_path):
    path = tmp_path / "many-parameters.sp"
    assignments = [f".param p{index}=p{index + 1}" for index in range(500)]
    assignments.append(".param p500=1")
    path.write_text("\n".join((*assignments, "R1 a 0 p0", ".end")), encoding="utf-8")
    result = flatten(analyze_file(path))
    assert not result.has_errors
    assert "R1 a 0 1" in format_deck(result.statements)


@pytest.mark.parametrize(
    "values",
    [
        {"max_depth": True},
        {"max_output_statements": 1.5},
        {"max_provenance_records": 0},
    ],
)
def test_elaboration_limits_require_positive_integers(values):
    with pytest.raises(ValueError, match="positive integer"):
        ElaborationLimits(**values)


def test_flatten_stops_at_output_and_provenance_limits(tmp_path):
    deck = tmp_path / "branching.sp"
    deck.write_text(
        ".subckt leaf a b\nR1 a b 1k\n.ends\n"
        ".subckt fan a b\nX1 a b leaf\nX2 a b leaf\nX3 a b leaf\n.ends\n"
        "X1 a b fan\nX2 a b fan\nX3 a b fan\n.end\n",
        encoding="utf-8",
    )
    analysis = analyze_file(deck)
    output_limited = flatten(analysis, limits=ElaborationLimits(max_output_statements=5))
    assert len(output_limited.statements) == 5
    assert len(output_limited.provenance) == 5
    assert "ST3009" in {item.code for item in output_limited.diagnostics}

    provenance_limited = flatten(analysis, limits=ElaborationLimits(max_provenance_records=2))
    assert len(provenance_limited.statements) == 2
    assert len(provenance_limited.provenance) == 2
    assert "ST3010" in {item.code for item in provenance_limited.diagnostics}


def test_flatten_counts_passthrough_and_bounds_depth(tmp_path):
    passthrough = tmp_path / "passthrough.sp"
    passthrough.write_text("* one\n* two\n.end\n", encoding="utf-8")
    limited = flatten(analyze_file(passthrough), limits=ElaborationLimits(max_output_statements=1))
    assert len(limited.statements) == 1
    assert "ST3009" in {item.code for item in limited.diagnostics}

    nested = tmp_path / "nested.sp"
    nested.write_text(
        ".subckt bottom a b\nR1 a b 1k\n.ends\n"
        ".subckt middle a b\nX1 a b bottom\n.ends\n"
        "X1 a b middle\n.end\n",
        encoding="utf-8",
    )
    depth_limited = flatten(analyze_file(nested), limits=ElaborationLimits(max_depth=1))
    assert depth_limited.statements == ()
    assert "ST3011" in {item.code for item in depth_limited.diagnostics}
