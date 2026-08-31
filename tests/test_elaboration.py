from pathlib import Path

from spicetrellis.api import analyze_file, flatten, format_deck
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
