from pathlib import Path

from spicetrellis.api import analyze_file, inventory

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_project_loads_includes_and_builds_inventory():
    analysis = analyze_file(FIXTURES / "valid" / "top.sp")
    assert not analysis.has_errors
    assert len(analysis.dependencies) == 2
    result = inventory(analysis).as_dict()
    assert result["files"] == 2
    assert result["includes"] == 1
    assert result["element_families"] == {"C": 1, "R": 2, "V": 1, "X": 1}
    assert result["subcircuits"][0]["name"] == "divider"
    assert result["parameters"] == ["gain", "RDOWN", "RUP", "supply"]


def test_semantic_errors_are_collected_in_one_pass():
    analysis = analyze_file(FIXTURES / "invalid" / "semantic.sp")
    codes = {item.code for item in analysis.diagnostics}
    assert {"ST2103", "ST2203", "ST2206", "ST2207", "ST2208"} <= codes


def test_include_cycle_is_rejected(tmp_path):
    (tmp_path / "a.sp").write_text('.include "b.sp"\n', encoding="utf-8")
    (tmp_path / "b.sp").write_text('.include "a.sp"\n', encoding="utf-8")
    analysis = analyze_file(tmp_path / "a.sp")
    assert any(item.code == "ST2003" for item in analysis.diagnostics)


def test_include_escape_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside_for_spicetrellis.sp"
    outside.write_text("R1 a 0 1k\n", encoding="utf-8")
    try:
        (tmp_path / "top.sp").write_text(f'.include "../{outside.name}"\n.end\n', encoding="utf-8")
        analysis = analyze_file(tmp_path / "top.sp")
        assert any(item.code == "ST2004" for item in analysis.diagnostics)
    finally:
        outside.unlink()


def test_explicit_include_root_can_authorize_a_sibling(tmp_path):
    project = tmp_path / "project"
    shared = tmp_path / "shared"
    project.mkdir()
    shared.mkdir()
    (shared / "cell.sp").write_text(".subckt cell a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    (project / "top.sp").write_text(
        '.include "../shared/cell.sp"\nX1 a b cell\n.end\n', encoding="utf-8"
    )
    analysis = analyze_file(project / "top.sp", include_roots=(tmp_path,))
    assert not analysis.has_errors


def test_missing_and_non_utf8_files_are_diagnostics(tmp_path):
    missing = analyze_file(tmp_path / "missing.sp")
    assert missing.deck is None
    assert missing.diagnostics[0].code == "ST2002"
    bad = tmp_path / "bad.sp"
    bad.write_bytes(b"\xff\xfe\x00")
    invalid = analyze_file(bad)
    assert invalid.deck is None
    assert invalid.diagnostics[0].code == "ST2002"


def test_subcircuit_recursion_and_unclosed_scope_are_rejected(tmp_path):
    deck = tmp_path / "recursive.sp"
    deck.write_text(
        ".subckt a p n\nX1 p n b\n.ends\n"
        ".subckt b p n\nX1 p n a\n.ends\n"
        ".subckt open p n\nR1 p n 1k\n",
        encoding="utf-8",
    )
    analysis = analyze_file(deck)
    codes = {item.code for item in analysis.diagnostics}
    assert "ST2209" in codes
    assert "ST2204" in codes


def test_duplicate_subcircuit_and_unmatched_ends_are_rejected(tmp_path):
    deck = tmp_path / "duplicates.sp"
    deck.write_text(
        ".ends\n.subckt a p n\n.ends\n.subckt A p n\n.ends\n",
        encoding="utf-8",
    )
    analysis = analyze_file(deck)
    codes = {item.code for item in analysis.diagnostics}
    assert "ST2202" in codes
    assert "ST2205" in codes


def test_nested_subcircuit_is_rejected(tmp_path):
    deck = tmp_path / "nested.sp"
    deck.write_text(
        ".subckt outer p n\n.subckt inner p n\n.ends inner\n.ends outer\n",
        encoding="utf-8",
    )
    analysis = analyze_file(deck)
    assert any(item.code == "ST2201" for item in analysis.diagnostics)


def test_duplicate_formal_pins_are_rejected_before_elaboration(tmp_path):
    deck = tmp_path / "duplicate-pins.sp"
    deck.write_text(
        ".subckt cell input INPUT\nR1 input 0 1k\n.ends\nX1 a b cell\n.end\n",
        encoding="utf-8",
    )
    analysis = analyze_file(deck)
    assert any(item.code == "ST2212" for item in analysis.diagnostics)


def test_end_in_include_is_terminal_for_expanded_project(tmp_path):
    (tmp_path / "included.sp").write_text("R1 a 0 1k\n.end\n", encoding="utf-8")
    top = tmp_path / "top.sp"
    top.write_text('.include "included.sp"\nR2 b 0 2k\n.end\n', encoding="utf-8")
    analysis = analyze_file(top)
    assert any(item.code == "ST2213" for item in analysis.diagnostics)
    assert analysis.deck is not None
    assert not any(getattr(item, "name", None) == "R2" for item in analysis.deck.top)
