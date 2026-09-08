from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from spicetrellis import (
    CircuitIR,
    analyze_file,
    build_ir,
    dump_ir,
    load_ir,
    load_ir_text,
    validate_ir,
    write_ir,
)
from spicetrellis import ir as ir_module
from spicetrellis.cli import main
from spicetrellis.ir import MAX_IR_BYTES, CircuitIRError

ROOT = Path(__file__).parents[1]
VALID = Path(__file__).parent / "fixtures" / "valid" / "top.sp"


def _valid_ir() -> CircuitIR:
    return build_ir(analyze_file(VALID))


def _document() -> dict[str, object]:
    return copy.deepcopy(_valid_ir().as_dict())


def test_ir_is_stable_explicit_and_round_trips() -> None:
    circuit = _valid_ir()

    assert circuit.entry == "top.sp"
    assert circuit.dependencies == ("library/passives.sp", "top.sp")
    assert [module.name for module in circuit.modules] == ["$top", "divider"]
    assert circuit.instance_count == 5
    assert circuit.losses == ()
    assert circuit.modules[0].instances[1].id == "$top::x1"
    assert circuit.modules[1].parameters[0].expression == "10k"
    assert all(not Path(item.source.file).is_absolute() for item in circuit.modules[0].instances)

    rendered = dump_ir(circuit)
    assert rendered == dump_ir(circuit)
    assert circuit.fingerprint == "ebed90a91d4623b2b6f909d68bfe12b9f605a17e862d09ae1b3544f25daffb48"
    assert load_ir_text(rendered) == circuit
    assert load_ir_text(rendered).fingerprint == circuit.fingerprint


def test_models_have_scope_and_canonical_identity(tmp_path: Path) -> None:
    deck = tmp_path / "model.sp"
    deck.write_text(
        ".model NM NMOS level=1\n"
        ".subckt stage d g s b\n"
        ".model PM PMOS level=1\n"
        "M1 d g s b PM W=2u L=180n\n"
        ".ends stage\n"
        "Mtop d g s b NM W=1u L=180n\n"
        ".end\n",
        encoding="utf-8",
    )

    circuit = build_ir(analyze_file(deck))

    assert [(item.id, item.module, item.kind) for item in circuit.models] == [
        ("$top::model::nm", "$top", "NMOS"),
        ("stage::model::pm", "stage", "PMOS"),
    ]
    assert circuit.models[1].source_form == "level=1"
    assert load_ir_text(dump_ir(circuit)) == circuit


def test_nested_canonical_expressions_round_trip(tmp_path: Path) -> None:
    deck = tmp_path / "nested-expression.sp"
    deck.write_text(".param x={1+2*3}\nR1 a 0 {x}\n.end\n", encoding="utf-8")

    circuit = build_ir(analyze_file(deck))

    assert circuit.modules[0].parameters[0].expression == "{1 + {2 * 3}}"
    assert load_ir_text(dump_ir(circuit)) == circuit


def test_duplicate_model_in_one_scope_is_rejected_as_ambiguous(tmp_path: Path) -> None:
    deck = tmp_path / "duplicate-model.sp"
    deck.write_text(".model N NMOS\n.model n NMOS\n.end\n", encoding="utf-8")
    analysis = analyze_file(deck)
    assert not analysis.has_errors

    with pytest.raises(CircuitIRError, match="model names"):
        build_ir(analysis)


def test_opaque_cards_are_declared_losses_not_silent_drops(tmp_path: Path) -> None:
    deck = tmp_path / "analysis.sp"
    deck.write_text("R1 in 0 1k\n.tran 1n 10n\n.end\n", encoding="utf-8")
    circuit = build_ir(analyze_file(deck))

    assert len(circuit.losses) == 1
    assert circuit.losses[0].module == "$top"
    assert circuit.losses[0].card == ".tran 1n 10n"
    assert circuit.losses[0].reason


def test_ir_identity_does_not_depend_on_checkout_path(tmp_path: Path) -> None:
    rendered: list[str] = []
    for checkout in (tmp_path / "first", tmp_path / "second"):
        project = checkout / "project"
        shared = checkout / "shared"
        project.mkdir(parents=True)
        shared.mkdir()
        (shared / "cell.sp").write_text(
            ".subckt cell a b\nRcell a b 3k\n.ends cell\n", encoding="utf-8"
        )
        entry = project / "top.sp"
        entry.write_text('.include "../shared/cell.sp"\nX1 in out cell\n.end\n', encoding="utf-8")
        analysis = analyze_file(entry, include_roots=(project, shared))
        assert not analysis.has_errors
        rendered.append(dump_ir(build_ir(analysis)))

    assert rendered[0] == rendered[1]
    document = json.loads(rendered[0])
    external = next(item for item in document["dependencies"] if item.startswith("external/"))
    assert len(external.split("/")[1]) == 64


def test_non_ascii_or_spaced_source_names_are_portably_percent_encoded(tmp_path: Path) -> None:
    library = tmp_path / "cells" / "é load.sp"
    library.parent.mkdir()
    library.write_text(".subckt cell a b\nR1 a b 1k\n.ends cell\n", encoding="utf-8")
    entry = tmp_path / "top.sp"
    entry.write_text('.include "cells/é load.sp"\nX1 a b cell\n.end\n', encoding="utf-8")

    circuit = build_ir(analyze_file(entry))

    assert "cells/%C3%A9%20load.sp" in circuit.dependencies
    assert circuit.modules[1].source is not None
    assert circuit.modules[1].source.file == "cells/%C3%A9%20load.sp"
    assert load_ir_text(dump_ir(circuit)) == circuit


def test_external_dependency_requires_the_analyzed_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shared = tmp_path / "shared"
    project.mkdir()
    shared.mkdir()
    external = shared / "cell.sp"
    external.write_text(".subckt cell a b\nR1 a b 1k\n.ends cell\n", encoding="utf-8")
    entry = project / "top.sp"
    entry.write_text('.include "../shared/cell.sp"\nX1 a b cell\n.end\n', encoding="utf-8")
    analysis = analyze_file(entry, include_roots=(project, shared))
    incomplete = replace(
        analysis,
        source_files=tuple(item for item in analysis.source_files if item[0] != external.resolve()),
    )

    with pytest.raises(CircuitIRError, match="has no source snapshot"):
        build_ir(incomplete)

    # In-tree paths remain reproducible without content addressing and can be
    # recovered from the semantic dependency closure alone.
    assert build_ir(replace(analyze_file(VALID), source_files=())).entry == "top.sp"


def test_ir_file_api_uses_deterministic_newlines(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "circuit.json"
    write_ir(_valid_ir(), destination)

    assert destination.read_bytes().endswith(b"\n")
    assert b"\r\n" not in destination.read_bytes()
    assert load_ir(destination) == _valid_ir()

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_ir(_valid_ir(), destination)
    write_ir(_valid_ir(), destination, force=True)
    assert load_ir(destination) == _valid_ir()


def test_in_memory_values_are_checked_before_hashing_or_writing() -> None:
    with pytest.raises(CircuitIRError, match="invalid in-memory"):
        validate_ir(None)  # type: ignore[arg-type]

    invalid_path = replace(_valid_ir(), entry="../outside.sp")
    with pytest.raises(CircuitIRError, match="relative POSIX"):
        validate_ir(invalid_path)
    with pytest.raises(CircuitIRError, match="relative POSIX"):
        dump_ir(invalid_path)
    with pytest.raises(CircuitIRError, match="relative POSIX"):
        _ = invalid_path.fingerprint

    circuit = _valid_ir()
    invalid_type = replace(circuit, modules=list(circuit.modules))
    with pytest.raises(CircuitIRError, match="non-canonical value types"):
        validate_ir(invalid_type)


def test_public_schema_accepts_the_producer_output() -> None:
    schema = json.loads(
        (ROOT / "docs" / "schemas" / "circuit-ir-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_valid_ir().as_dict())

    shared = load_ir(ROOT / "interop" / "fixtures" / "minimal-v1.json")
    Draft202012Validator(schema).validate(shared.as_dict())
    assert shared.fingerprint == "9a37a5fd9b60ffdf5f2277eb5cb3048c7f2acd07a1f9e99cfd5cf314a5927c44"


def test_shared_cross_language_rejection_corpus() -> None:
    valid = (ROOT / "interop" / "fixtures" / "minimal-v1.json").read_text(encoding="utf-8")
    corpus = json.loads(
        (ROOT / "interop" / "fixtures" / "rejection-corpus-v1.json").read_text(encoding="utf-8")
    )
    assert corpus["schema"] == "org.spicetrellis.circuit-ir-rejection-corpus"
    assert corpus["schema_version"] == 1
    assert len(corpus["cases"]) >= 10
    for case in corpus["cases"]:
        mutated = valid.replace(case["old"], case["new"], 1)
        assert mutated != valid, case["name"]
        try:
            load_ir_text(mutated)
        except CircuitIRError:
            continue
        pytest.fail(f"shared rejection case was accepted: {case['name']}")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(extra=True), "unknown extra"),
        (lambda value: value.pop("entry"), "missing entry"),
        (lambda value: value.update(schema_version=True), "unsupported"),
        (lambda value: value.update(schema_version=2), "unsupported"),
        (lambda value: value.update(entry="absent.sp"), "entry must be present"),
        (lambda value: value.update(dependencies=["top.sp", "top.sp"]), "unique"),
        (lambda value: value.update(dependencies=["C:\\top.sp"]), "relative POSIX"),
        (lambda value: value.update(global_nodes=["VSS", "vss"]), "global node"),
        (lambda value: value["modules"][0].update(name="wrong"), "begin with"),
        (
            lambda value: value["modules"][0]["instances"][0].update(id="wrong"),
            "non-canonical ID",
        ),
        (
            lambda value: value["modules"][0]["instances"][0]["source"].update(
                start=[2, 1], end=[1, 1]
            ),
            "precedes",
        ),
        (
            lambda value: value["modules"][0]["parameters"][0].update(expression="{1+2}"),
            "not canonical",
        ),
        (
            lambda value: value["losses"].append(
                {
                    "module": "missing",
                    "reason": "test",
                    "card": ".x",
                    "source": {"file": "top.sp", "start": [1, 1], "end": [1, 2]},
                }
            ),
            "unknown module",
        ),
    ],
)
def test_strict_decoder_rejects_ambiguous_documents(mutation, message: str) -> None:
    document = _document()
    mutation(document)

    with pytest.raises(CircuitIRError, match=message):
        load_ir_text(json.dumps(document))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(modules={}), "modules must be an array"),
        (lambda value: value.update(modules=[]), "begin with"),
        (lambda value: value["modules"].append(copy.deepcopy(value["modules"][1])), "module names"),
        (
            lambda value: value["modules"][1].update(ports=["IN", "in"]),
            "duplicate ports",
        ),
        (
            lambda value: value["modules"][0].update(
                source=copy.deepcopy(value["modules"][1]["source"])
            ),
            "only the \\$top",
        ),
        (
            lambda value: value["modules"][1].update(source=None),
            "only the \\$top",
        ),
        (
            lambda value: value["modules"][0]["parameters"].append(
                copy.deepcopy(value["modules"][0]["parameters"][0])
            ),
            "duplicate parameters",
        ),
        (
            lambda value: value["modules"][0]["instances"].append(
                copy.deepcopy(value["modules"][0]["instances"][0])
            ),
            "duplicate instances",
        ),
        (
            lambda value: value["modules"][1]["instances"][0].update(
                id=value["modules"][0]["instances"][0]["id"]
            ),
            "globally unique",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(model=3),
            "must be a non-empty string",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(value="1 +"),
            "value is invalid",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(value="{1+2}"),
            "value is not canonical",
        ),
        (
            lambda value: value["modules"][0]["parameters"][0].update(expression="1 +"),
            "expression is invalid",
        ),
        (
            lambda value: value["modules"][0]["instances"][0]["source"].update(start=[1]),
            "line and column",
        ),
        (
            lambda value: value["modules"][0]["instances"][0]["source"].update(start=[True, 1]),
            "integer from 1 through",
        ),
        (
            lambda value: value["modules"][0]["instances"][0]["source"].update(file="a/../b"),
            "relative POSIX",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(name="bad\u0000name"),
            "must not contain NUL",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(name="1bad"),
            "portable SPICE identifier",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(family="Q"),
            "not supported",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(
                name="Rwrong", id="$top::rwrong"
            ),
            "element family",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(connections=["nét"]),
            "printable ASCII",
        ),
        (
            lambda value: value["modules"][0]["instances"][1]["parameters"].append(
                copy.deepcopy(value["modules"][0]["instances"][1]["parameters"][0])
            ),
            "duplicate parameters",
        ),
        (
            lambda value: value["modules"][0]["instances"][2].update(source_form="2p"),
            "invalid C fields",
        ),
        (
            lambda value: value["modules"][0]["instances"][0].update(value="1"),
            "invalid V fields",
        ),
        (
            lambda value: value["modules"][0]["instances"][1].update(model="missing"),
            "unknown subcircuit",
        ),
        (
            lambda value: value["modules"][0]["instances"][1].update(value="1"),
            "invalid X fields",
        ),
        (
            lambda value: value["modules"][0]["instances"][1].update(connections=["one"]),
            "port count",
        ),
        (
            lambda value: value["modules"][0]["instances"][2]["source"].update(file="foreign.sp"),
            "outside dependencies",
        ),
        (
            lambda value: value["modules"][0]["parameters"][0]["source"].update(file="foreign.sp"),
            "outside dependencies",
        ),
        (
            lambda value: value["modules"][0]["instances"][1]["parameters"][0]["source"].update(
                file="foreign.sp"
            ),
            "outside dependencies",
        ),
    ],
)
def test_strict_decoder_rejects_invalid_nested_values(mutation, message: str) -> None:
    document = _document()
    mutation(document)

    with pytest.raises(CircuitIRError, match=message):
        load_ir_text(json.dumps(document))


def test_model_scope_and_identity_are_independently_validated(tmp_path: Path) -> None:
    deck = tmp_path / "model.sp"
    deck.write_text(".model NM NMOS level=1\nM1 d g s b NM\n.end\n", encoding="utf-8")
    document = build_ir(analyze_file(deck)).as_dict()
    model = document["models"][0]

    duplicate = copy.deepcopy(document)
    duplicate["models"].append(copy.deepcopy(model))
    with pytest.raises(CircuitIRError, match="globally unique"):
        load_ir_text(json.dumps(duplicate))

    foreign = copy.deepcopy(document)
    foreign["models"][0]["module"] = "missing"
    with pytest.raises(CircuitIRError, match="unknown module"):
        load_ir_text(json.dumps(foreign))

    noncanonical = copy.deepcopy(document)
    noncanonical["models"][0]["id"] = "$top::model::other"
    with pytest.raises(CircuitIRError, match="non-canonical ID"):
        load_ir_text(json.dumps(noncanonical))

    missing_model = copy.deepcopy(document)
    missing_model["models"] = []
    with pytest.raises(CircuitIRError, match="unknown model"):
        load_ir_text(json.dumps(missing_model))

    malformed_mos = copy.deepcopy(document)
    malformed_mos["modules"][0]["instances"][0]["connections"] = ["d", "g", "s"]
    with pytest.raises(CircuitIRError, match="invalid M fields"):
        load_ir_text(json.dumps(malformed_mos))


@pytest.mark.parametrize(
    "text",
    [
        '{"schema": "first", "schema": "second"}',
        '{"schema": NaN}',
        "[",
        "[" * 2_000,
    ],
)
def test_json_decoder_rejects_duplicate_nonfinite_malformed_and_deep_json(text: str) -> None:
    with pytest.raises(CircuitIRError, match="invalid circuit IR JSON"):
        load_ir_text(text)


def test_json_root_must_be_an_object() -> None:
    with pytest.raises(CircuitIRError, match="must be an object"):
        load_ir_text("[]")


def test_decoder_enforces_the_serialized_size_limit() -> None:
    with pytest.raises(CircuitIRError, match="byte limit"):
        load_ir_text(" " * (MAX_IR_BYTES + 1))

    with pytest.raises(CircuitIRError, match="invalid circuit IR text"):
        load_ir_text('"\ud800"')

    escaped = (
        (ROOT / "interop" / "fixtures" / "minimal-v1.json")
        .read_text(encoding="utf-8")
        .replace('"value": "1k"', '"value": "\\ud800"')
    )
    with pytest.raises(CircuitIRError, match="Unicode scalar"):
        load_ir_text(escaped)


def test_dump_size_limit_includes_pretty_whitespace_and_final_newline(monkeypatch) -> None:
    circuit = _valid_ir()
    compact_without_newline = json.dumps(
        circuit.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    monkeypatch.setattr(ir_module, "MAX_IR_BYTES", len(compact_without_newline.encode("utf-8")))

    with pytest.raises(CircuitIRError, match="serialized circuit IR"):
        dump_ir(circuit, pretty=False)
    with pytest.raises(CircuitIRError, match="serialized circuit IR"):
        dump_ir(circuit, pretty=True)


def test_collection_and_string_limits_fail_before_unbounded_work(monkeypatch) -> None:
    document = _document()
    monkeypatch.setattr(ir_module, "MAX_MODULES", 1)
    with pytest.raises(CircuitIRError, match="1-item limit"):
        load_ir_text(json.dumps(document))

    monkeypatch.setattr(ir_module, "MAX_MODULES", 4_096)
    monkeypatch.setattr(ir_module, "MAX_STRING_CHARS", 2)
    with pytest.raises(CircuitIRError, match="2-character limit"):
        load_ir_text(json.dumps(document))


def test_total_instance_limit_applies_across_modules(monkeypatch) -> None:
    analysis = analyze_file(VALID)
    document = _document()
    monkeypatch.setattr(ir_module, "MAX_INSTANCES", 3)
    with pytest.raises(CircuitIRError, match="3-instance limit"):
        build_ir(analysis)

    with pytest.raises(CircuitIRError, match="3-instance limit"):
        load_ir_text(json.dumps(document))


def test_build_rejects_an_analysis_with_errors() -> None:
    invalid = Path(__file__).parent / "fixtures" / "invalid" / "semantic.sp"
    with pytest.raises(CircuitIRError, match="without errors"):
        build_ir(analyze_file(invalid))


def test_cached_validation_does_not_cross_dataclass_replacement() -> None:
    circuit = _valid_ir()
    dump_ir(circuit)
    changed = replace(circuit, entry="missing.sp")
    with pytest.raises(CircuitIRError, match="entry must be present"):
        dump_ir(changed)


def test_export_and_check_cli_cover_file_stdout_compact_and_loss_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "circuit.ir.json"
    assert main(["export-ir", str(VALID), "--compact", "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8").count("\n") == 1

    assert main(["check-ir", str(output)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["fingerprint"] == load_ir(output).fingerprint
    assert checked["instances"] == 5

    lossy = tmp_path / "lossy.sp"
    lossy.write_text("R1 in 0 1k\n.option post\n.end\n", encoding="utf-8")
    assert main(["export-ir", str(lossy), "--require-lossless"]) == 1
    assert "declared loss" in capsys.readouterr().err
    assert main(["export-ir", str(lossy), "-o", str(output), "--force"]) == 0
    assert main(["check-ir", str(output), "--require-lossless"]) == 1
    assert json.loads(capsys.readouterr().out)["losses"] == 1


def test_ir_cli_reports_semantic_and_document_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid_deck = Path(__file__).parent / "fixtures" / "invalid" / "semantic.sp"
    assert main(["export-ir", str(invalid_deck)]) == 1
    assert "ST2103" in capsys.readouterr().err

    invalid_ir = tmp_path / "invalid.json"
    invalid_ir.write_text("{}", encoding="utf-8")
    assert main(["check-ir", str(invalid_ir)]) == 2
    assert "circuit IR" in capsys.readouterr().err

    exponent_ir = tmp_path / "exponent.json"
    exponent_ir.write_text(
        (ROOT / "interop" / "fixtures" / "minimal-v1.json")
        .read_text(encoding="utf-8")
        .replace('"value": "1k"', '"value": "1e9999999999999999999"'),
        encoding="utf-8",
    )
    assert main(["check-ir", str(exponent_ir)]) == 2
    captured = capsys.readouterr()
    assert "exponent magnitude" in captured.err
    assert "Traceback" not in captured.err

    flat_deck = tmp_path / "flat-expression.sp"
    flat_output = tmp_path / "flat-expression.ir.json"
    flat_deck.write_text("R1 a 0 " + "+".join(["1"] * 2_000) + "\n.end\n", encoding="utf-8")
    assert main(["export-ir", str(flat_deck), "-o", str(flat_output)]) == 1
    captured = capsys.readouterr()
    assert "1024-token limit" in captured.err
    assert "Traceback" not in captured.err
    assert not flat_output.exists()


def test_export_ir_never_overwrites_its_input_even_when_forced(tmp_path, capsys) -> None:
    source = tmp_path / "source.sp"
    original = b"R1 in 0 1k\n.end\n"
    source.write_bytes(original)

    assert main(["export-ir", str(source), "-o", str(source)]) == 2
    assert source.read_bytes() == original
    assert "aliases an input" in capsys.readouterr().err
    assert main(["export-ir", str(source), "-o", str(source), "--force"]) == 2
    assert source.read_bytes() == original
    assert "aliases an input" in capsys.readouterr().err


def test_file_decoder_reports_missing_oversized_and_directory_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    with pytest.raises(CircuitIRError, match="cannot read"):
        load_ir(tmp_path / "missing.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ir_module, "MAX_IR_BYTES", 1)
    with pytest.raises(CircuitIRError, match="byte limit"):
        load_ir(oversized)

    monkeypatch.setattr(ir_module, "MAX_IR_BYTES", MAX_IR_BYTES)
    with pytest.raises(CircuitIRError, match="cannot read"):
        load_ir(tmp_path)
