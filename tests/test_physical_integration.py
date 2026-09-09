"""Public API, CLI, shipped schema and runnable documentation agree."""

import json
import runpy
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from spicetrellis import PhysicalError, dump_physical, load_physical, physical_data, physical_digest
from spicetrellis.cli import main
from spicetrellis.geometry import Point, Rectangle
from spicetrellis.physical import (
    DistanceUnit,
    LayerPurpose,
    LayerSpec,
    LayoutView,
    PhysicalCell,
    PhysicalLibrary,
    PhysicalShape,
    Technology,
)


def model():
    technology = Technology("test", (LayerSpec("m1", 1, 0, LayerPurpose.DRAWING),))
    cell = PhysicalCell(
        "top", layout=LayoutView((PhysicalShape("m1", Rectangle(Point(0, 0), 3, 7)),))
    )
    return PhysicalLibrary("demo", DistanceUnit.NANOMETER, technology, (cell,))


def test_source_schema_accepts_the_real_wire_format_and_is_packaged():
    schema = json.loads(
        Path("docs/schemas/physical-library-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    data = physical_data(model())
    validator.validate(data)
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    mapping = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert (
        mapping["docs/schemas/physical-library-v1.schema.json"]
        == "spicetrellis/schemas/physical-library-v1.schema.json"
    )
    for field, value in (("version", True), ("name", "x\n"), ("unit", "meter"), ("extra", 0)):
        invalid = {**data, field: value}
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    data["cells"][0]["layout"]["shapes"][0]["geometry"]["origin"] = [0, 0]
    with pytest.raises(ValidationError):
        validator.validate(data)


def test_check_physical_can_validate_or_materialize(tmp_path, capsys):
    path = tmp_path / "layout.json"
    path.write_text(dump_physical(model()), encoding="utf-8")
    assert main(["check-physical", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["content_sha256"] == physical_digest(model())
    assert summary["layout_expansion"] is None
    assert main(["check-physical", str(path), "--top", "top"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["layout_expansion"] == {
        "top": "top",
        "cells": 1,
        "shapes": 1,
        "annotations": 0,
        "bounds": ["0", "0", "3", "7"],
    }
    assert main(["check-physical", str(path), "--top", "top", "--max-points", "3"]) == 2
    assert "resource budget" in capsys.readouterr().err
    assert main(["check-physical", str(path), "--max-cells", "0"]) == 2
    assert "flatten limit" in capsys.readouterr().err


def test_empty_layout_cli_bounds_are_null(tmp_path, capsys):
    empty = PhysicalLibrary(
        "empty",
        DistanceUnit.ANGSTROM,
        Technology("t", ()),
        (PhysicalCell("top", layout=LayoutView()),),
    )
    path = tmp_path / "empty.json"
    path.write_text(dump_physical(empty), encoding="utf-8")
    assert main(["check-physical", str(path), "--top", "top"]) == 0
    assert json.loads(capsys.readouterr().out)["layout_expansion"]["bounds"] is None


def test_normalize_physical_preserves_inputs_and_requires_explicit_clobber(tmp_path, capsys):
    source, target = tmp_path / "in.json", tmp_path / "out.json"
    original = json.dumps(physical_data(model()), indent=2).encode("utf-8")
    source.write_bytes(original)
    assert main(["normalize-physical", str(source)]) == 0
    assert capsys.readouterr().out == dump_physical(model())
    assert main(["normalize-physical", str(source), "-o", str(target)]) == 0
    assert load_physical(target) == model()
    assert main(["normalize-physical", str(source), "-o", str(target)]) == 2
    assert main(["normalize-physical", str(source), "-o", str(target), "--force"]) == 0
    assert main(["normalize-physical", str(source), "-o", str(source), "--force"]) == 2
    assert source.read_bytes() == original
    assert main(["check-physical", str(tmp_path / "missing")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_documented_construction_example_executes():
    document = Path("docs/physical-library.md").read_text(encoding="utf-8")
    code = document.split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    exec(compile(code, "docs/physical-library.md", "exec"), namespace)
    assert namespace["flat"].expanded_cells == 3
    assert isinstance(namespace["library"], PhysicalLibrary)
    assert issubclass(PhysicalError, ValueError)


def test_release_smoke_loads_the_real_installed_schema_and_materializes_geometry():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert '"spicetrellis/schemas/physical-library-v1.schema.json"' in workflow
    assert '"docs/schemas/physical-library-v1.schema.json"' in workflow
    assert '"physical-library-v1.schema.json": "org.spicetrellis.physical-library"' in workflow
    assert 'spicetrellis.flatten_layout(physical, "top")' in workflow
    assert '"check-physical", str(physical_path), "--top", "top"' in workflow


def test_original_physical_example_has_exact_documented_expansion(tmp_path, capsys):
    from spicetrellis import flatten_layout, write_physical
    from spicetrellis.geometry import Bounds

    example = runpy.run_path("examples/physical_library.py")
    library = example["make_library"]()
    output = tmp_path / "library.json"
    write_physical(library, output)
    assert load_physical(output) == library
    flat = flatten_layout(library, "top")
    assert flat.expanded_cells == 4
    assert len(flat.shapes) == 10
    assert len(flat.annotations) == 4
    assert flat.bounds == Bounds(-30, -30, 190, 30)
    assert tuple(shape.instance_path for shape in flat.shapes if shape.shape.net == "body") == (
        ("plain",),
        ("rotated",),
        ("reflected",),
    )
    assert main(["check-physical", str(output), "--top", "top"]) == 0
    assert json.loads(capsys.readouterr().out)["layout_expansion"]["shapes"] == 10
