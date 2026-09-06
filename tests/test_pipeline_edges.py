import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from spicetrellis.api import analyze_file, flatten, format_deck, inventory, parse_text
from spicetrellis.cli import main
from spicetrellis.emit import format_diagnostics, to_json
from spicetrellis.model import Element, Opaque


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_every_supported_statement_has_canonical_output():
    source = """* complete surface
.include "directory with spaces/models.sp" ; local
.param width = 2u length=180n
.global 0 VDD
.model nch nmos level=1
.subckt cell d g s b SCALE=1
M1 d g s b nch W={width*SCALE} L={length}
V1 d s DC {SCALE}
I1 g s 1u
X1 d g nested ratio = 2
.ends cell
.option unsupported
.end
"""
    deck = parse_text(source)
    rendered = format_deck(deck)
    assert '* complete surface\n.include "directory with spaces/models.sp" ; local' in rendered
    assert ".param width=2u length=180n" in rendered
    assert ".global 0 VDD" in rendered
    assert ".model nch nmos level=1" in rendered
    assert ".subckt cell d g s b SCALE=1" in rendered
    assert "M1 d g s b nch W={width * SCALE} L=length" in rendered
    assert "X1 d g nested ratio=2" in rendered
    assert ".ends cell" in rendered
    assert ".option unsupported" in rendered
    assert rendered.endswith(".end\n")


def test_forward_parameters_are_resolved_without_source_order_dependency(tmp_path):
    deck = _write(
        tmp_path / "forward.sp",
        ".param result={base+1} base=2\nR1 a 0 {result}\n.end\n",
    )
    result = flatten(analyze_file(deck))
    assert not result.has_errors
    assert "R1 a 0 3" in format_deck(result.statements)


def test_nested_defaults_and_overrides_use_the_caller_environment(tmp_path):
    deck = _write(
        tmp_path / "defaults.sp",
        ".param global_scale=3\n"
        ".subckt cell a b VALUE={later*global_scale} later=2\n"
        "R1 a internal {VALUE}\nR2 internal b {later}\n"
        ".ends\n"
        "Xtop left right cell later=4\n.end\n",
    )
    result = flatten(analyze_file(deck))
    assert not result.has_errors
    rendered = format_deck(result.statements)
    assert "Xtop__R1 left Xtop__n__internal 12" in rendered
    assert "Xtop__R2 Xtop__n__internal right 4" in rendered


def test_global_nodes_are_not_hierarchically_renamed(tmp_path):
    deck = _write(
        tmp_path / "global.sp",
        ".global VDD\n.subckt cell a\nR1 a vdd 1k\n.ends\nX1 out cell\n.end\n",
    )
    rendered = format_deck(flatten(analyze_file(deck)).statements)
    assert "X1__R1 out vdd 1000" in rendered
    assert "X1__n__vdd" not in rendered


def test_elaboration_reports_primitive_expression_errors(tmp_path):
    deck = _write(
        tmp_path / "unresolved.sp",
        "R1 a 0 {missing}\n"
        "V1 a 0 DC {missing}\n"
        "M1 a a 0 0 nch W={missing} L=1u\n"
        ".model nch nmos\n.end\n",
    )
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    result = flatten(analysis)
    assert result.has_errors
    assert {item.code for item in result.diagnostics} == {"ST3002", "ST3003", "ST3004"}


def test_invalid_instance_override_is_reported_during_elaboration(tmp_path):
    deck = _write(
        tmp_path / "override.sp",
        ".subckt cell a b VALUE=1k\nR1 a b {VALUE}\n.ends\nX1 a b cell VALUE={unknown}\n.end\n",
    )
    analysis = analyze_file(deck)
    assert not analysis.has_errors
    result = flatten(analysis)
    assert any(item.code == "ST3007" for item in result.diagnostics)


def test_unknown_and_duplicate_instance_overrides_are_rejected(tmp_path):
    deck = _write(
        tmp_path / "bad_overrides.sp",
        ".subckt cell a b VALUE=1k\nR1 a b {VALUE}\n.ends\n"
        "X1 a b cell UNKNOWN=2 VALUE=3 VALUE=4\n.end\n",
    )
    codes = {item.code for item in analyze_file(deck).diagnostics}
    assert {"ST2210", "ST2211"} <= codes


def test_opaque_cards_inside_a_subcircuit_remain_visible_after_expansion(tmp_path):
    deck = _write(
        tmp_path / "opaque.sp",
        ".subckt cell a b\n.option local\nR1 a b 1k\n.ends\nX1 a b cell\n.end\n",
    )
    result = flatten(analyze_file(deck))
    assert any(isinstance(item, Opaque) for item in result.statements)
    assert ".option local" in format_deck(result.statements)


@pytest.mark.parametrize(
    "deck_text",
    [
        ".param value={value+1}\n.end\n",
        ".param same=1\n.param SAME=2\n.end\n",
        ".param value={absent+1}\n.end\n",
        ".subckt a p n VALUE=1\n.param value=2\n.ends\n.end\n",
    ],
)
def test_parameter_scope_failures_are_diagnostics(tmp_path, deck_text):
    deck = _write(tmp_path / "parameters.sp", deck_text)
    codes = {item.code for item in analyze_file(deck).diagnostics}
    assert codes & {"ST2101", "ST2102", "ST2103"}


def test_self_recursive_subcircuit_is_detected(tmp_path):
    deck = _write(
        tmp_path / "self.sp",
        ".subckt loop a b\nXagain a b loop\n.ends\n.end\n",
    )
    assert any(item.code == "ST2209" for item in analyze_file(deck).diagnostics)


def test_repeated_include_preserves_semantics_and_reports_duplicate_definition(tmp_path):
    _write(tmp_path / "cell.sp", ".subckt cell a b\nR1 a b 1k\n.ends\n")
    top = _write(
        tmp_path / "top.sp",
        '.include "cell.sp"\n.include "cell.sp"\nX1 a b cell\n.end\n',
    )
    analysis = analyze_file(top)
    assert len(analysis.dependencies) == 2
    assert any(item.code == "ST2205" for item in analysis.diagnostics)


def test_inventory_for_failed_load_is_empty(tmp_path):
    result = inventory(analyze_file(tmp_path / "absent.sp"))
    assert result.as_dict() == {
        "files": 0,
        "includes": 0,
        "library_sections": 0,
        "subcircuits": [],
        "element_families": {},
        "parameters": [],
        "models": [],
    }


def test_diagnostic_text_and_json_have_source_evidence(tmp_path):
    deck = _write(tmp_path / "bad.sp", "Rbroken a\n")
    analysis = analyze_file(deck)
    text = format_diagnostics(analysis.diagnostics)
    assert f"{deck}:1:1: error ST1002" in text
    payload = json.loads(to_json(analysis.diagnostics))
    assert payload[0]["primary"]["filename"] == str(deck)
    assert payload[0]["primary"]["start_line"] == 1


def test_no_diagnostics_has_explicit_text():
    assert format_diagnostics(()) == "no diagnostics\n"


def test_cli_stdout_paths_and_invalid_formatting(tmp_path, capsys):
    valid = _write(tmp_path / "valid.sp", "R1 a 0 1k\n.end\n")
    assert main(["flatten", str(valid)]) == 0
    assert "R1 a 0 1000" in capsys.readouterr().out
    assert main(["format", str(valid)]) == 0
    assert capsys.readouterr().out == "R1 a 0 1k\n.end\n"

    invalid = _write(tmp_path / "invalid.sp", "R1 a\n")
    assert main(["format", str(invalid)]) == 1
    assert "ST1002" in capsys.readouterr().err
    assert main(["inventory", str(invalid)]) == 1
    assert "ST1002" in capsys.readouterr().err


def test_include_path_with_spaces_is_supported(tmp_path):
    library = _write(
        tmp_path / "directory with spaces" / "cell library.sp",
        ".subckt cell a b\nR1 a b 1k\n.ends\n",
    )
    top = _write(
        tmp_path / "top deck.sp",
        f'.include "{library.relative_to(tmp_path)}"\nX1 a b cell\n.end\n',
    )
    assert not analyze_file(top).has_errors


def test_control_like_text_is_never_executed(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    deck = _write(
        tmp_path / "control.sp",
        f".control\nshell echo unsafe > {sentinel}\n.endc\n.end\n",
    )
    analysis = analyze_file(deck)
    assert not sentinel.exists()
    assert sum(item.code == "ST1101" for item in analysis.diagnostics) == 3


def test_cli_output_is_stable_across_hash_seeds():
    fixture = Path(__file__).parent / "fixtures" / "valid" / "top.sp"
    project_root = Path(__file__).parents[1]
    outputs = []
    for seed in ("1", "73"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = str(project_root / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "spicetrellis", "inventory", str(fixture)],
            cwd=project_root,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]


def test_element_names_are_unique_case_insensitively(tmp_path):
    deck = _write(tmp_path / "names.sp", "Rone a 0 1k\nRONE b 0 2k\n.end\n")
    analysis = analyze_file(deck)
    duplicate = next(item for item in analysis.diagnostics if item.code == "ST2206")
    assert duplicate.related


def test_syntax_json_retains_original_physical_card():
    deck = parse_text("R1 a b {1k\n+ * 2}\n", "continued.sp")
    payload = json.loads(to_json(deck))
    meta = payload["statements"][0]["meta"]
    assert meta["raw"] == "R1 a b {1k\n+ * 2}"
    assert len(meta["segments"]) == 2


def test_flattened_elements_are_real_supported_elements(tmp_path):
    deck = _write(
        tmp_path / "all.sp",
        ".subckt primitives a b\nR1 a b 1k\nC1 b 0 1n\nL1 a b 2n\n.ends\n"
        "X1 left right primitives\n.end\n",
    )
    result = flatten(analyze_file(deck))
    assert [item.family for item in result.statements if isinstance(item, Element)] == [
        "R",
        "C",
        "L",
    ]
