import json
from pathlib import Path

import pytest

from spicetrellis import __version__
from spicetrellis.cli import main
from spicetrellis.emit import format_deck, to_json
from spicetrellis.parser import parse_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_format_is_idempotent_and_preserves_comments():
    source = "* heading\nR1   a  0   1k ; load\n.end\n"
    first = format_deck(parse_text(source))
    second = format_deck(parse_text(first))
    assert first == second
    assert first == "* heading\nR1 a 0 1k ; load\n.end\n"


def test_json_renderer_is_stable_and_typed():
    first = to_json(parse_text("R1 a 0 1k\n", "memory.sp"))
    second = to_json(parse_text("R1 a 0 1k\n", "memory.sp"))
    assert first == second
    data = json.loads(first)
    assert data["type"] == "SyntaxDeck"
    assert data["statements"][0]["type"] == "Element"


def test_parse_and_inventory_commands(capsys):
    valid = FIXTURES / "valid" / "top.sp"
    assert main(["parse", str(valid)]) == 0
    assert json.loads(capsys.readouterr().out)["type"] == "SyntaxDeck"
    assert main(["inventory", str(valid)]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == 2


def test_lint_text_and_json_modes(capsys):
    invalid = FIXTURES / "invalid" / "semantic.sp"
    assert main(["lint", str(invalid)]) == 1
    assert "error ST2103" in capsys.readouterr().out
    assert main(["lint", str(invalid), "--json"]) == 1
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_flatten_writes_deck_and_provenance(tmp_path):
    output = tmp_path / "new output directory" / "flat deck.sp"
    provenance = tmp_path / "new map directory" / "map.json"
    assert (
        main(
            [
                "flatten",
                str(FIXTURES / "valid" / "top.sp"),
                "-o",
                str(output),
                "--provenance",
                str(provenance),
            ]
        )
        == 0
    )
    assert "X1__Rup" in output.read_text(encoding="utf-8")
    assert json.loads(provenance.read_text(encoding="utf-8"))[0]["output_name"] == "VDD"


def test_flatten_refuses_invalid_analysis(capsys):
    assert main(["flatten", str(FIXTURES / "invalid" / "semantic.sp")]) == 1
    assert "ST2103" in capsys.readouterr().err


def test_format_check_and_output(tmp_path, capsys):
    path = tmp_path / "deck.sp"
    path.write_text("R1   a 0 1k\n.end\n", encoding="utf-8")
    assert main(["format", str(path), "--check"]) == 1
    output = tmp_path / "formatted.sp"
    assert main(["format", str(path), "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == "R1 a 0 1k\n.end\n"
    assert capsys.readouterr().out == ""
    assert main(["format", str(output), "--check"]) == 0


def test_io_failure_uses_exit_code_two(capsys):
    assert main(["parse", "does-not-exist.sp"]) == 2
    assert "spice-trellis:" in capsys.readouterr().err


def test_direct_file_commands_reject_oversized_input_without_traceback(tmp_path, capsys):
    path = tmp_path / "oversized.sp"
    path.write_bytes(b"*" * (2 * 1024 * 1024 + 1))
    for command in ("parse", "format", "fuzz-smoke"):
        assert main([command, str(path)]) == 2
        assert "exceeds the" in capsys.readouterr().err


def test_version_and_required_command(capsys):
    with pytest.raises(SystemExit) as version:
        main(["--version"])
    assert version.value.code == 0
    assert __version__ in capsys.readouterr().out
    with pytest.raises(SystemExit) as missing:
        main([])
    assert missing.value.code == 2
