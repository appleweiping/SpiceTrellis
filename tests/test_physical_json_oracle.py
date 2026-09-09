"""Independent adversarial checks for physical-library JSON v1."""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path as FilePath

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from spicetrellis.geometry import Path as GeometryPath
from spicetrellis.geometry import Point, Polygon
from spicetrellis.physical import (
    DistanceUnit,
    LayerPurpose,
    LayerSpec,
    LayoutView,
    PhysicalCell,
    PhysicalError,
    PhysicalLibrary,
    PhysicalShape,
    Technology,
)
from spicetrellis.physical_json import dump_physical, load_physical_text

FIXTURES = FilePath(__file__).parents[1] / "interop" / "fixtures" / "physical-v1"
SCHEMA = FilePath(__file__).parents[1] / "docs" / "schemas" / "physical-library-v1.schema.json"


def test_bounded_dump_rejects_before_materializing_a_detached_wire_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wire cap must also bound temporary tree amplification on rejection."""
    import spicetrellis.physical_json as wire

    points = tuple(Point(index, 0) for index in range(100_000))
    shape = PhysicalShape("wire", GeometryPath(points, 1))
    library = PhysicalLibrary(
        "oracle",
        DistanceUnit.NANOMETER,
        Technology("oracle", (LayerSpec("wire", 1, 0, LayerPurpose.DRAWING),)),
        (PhysicalCell("top", layout=LayoutView((shape,))),),
    )
    monkeypatch.setattr(wire, "MAX_PHYSICAL_BYTES", 500)

    tracemalloc.start()
    try:
        with pytest.raises(PhysicalError, match="byte budget"):
            dump_physical(library)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 1_000_000


@pytest.mark.parametrize("name", ["rich-v1.json", "extreme-polygon-v1.json"])
def test_go_interop_fixtures_match_python_decoder_and_wire_schema(name: str) -> None:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    data = json.loads(text)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(data)
    library = load_physical_text(text)
    assert library.name == data["name"]


def test_extreme_interop_polygon_area_exceeds_fixed_width_without_overflow() -> None:
    text = (FIXTURES / "extreme-polygon-v1.json").read_text(encoding="utf-8")
    library = load_physical_text(text)
    assert library.cells[0].layout is not None
    geometry = library.cells[0].layout.shapes[0].geometry
    assert isinstance(geometry, Polygon)
    assert geometry.signed_double_area == 680564733841876926852962238568698216450
    assert geometry.area == 340282366920938463426481119284349108225


def test_shared_go_python_rejection_corpus_has_identical_fail_closed_outcome() -> None:
    corpus = json.loads((FIXTURES / "rejection-corpus-v1.json").read_text(encoding="utf-8"))
    assert corpus["schema"] == "org.spicetrellis.physical-library-rejection-corpus"
    assert corpus["version"] == 1
    assert len(corpus["cases"]) >= 20
    valid = (FIXTURES / "rich-v1.json").read_text(encoding="utf-8")
    for case in corpus["cases"]:
        mutated = valid.replace(case["old"], case["new"], 1)
        assert mutated != valid, case["name"]
        with pytest.raises(PhysicalError):
            load_physical_text(mutated)


@pytest.mark.parametrize(
    "wire_name,expected",
    [
        ("�", "�"),
        (r"\ufffd", "�"),
        (r"\ud83d\ude00", "😀"),
        (r"\\ud800", r"\ud800"),
    ],
)
def test_unicode_escape_semantics_match_go_without_surrogate_normalization(
    wire_name: str, expected: str
) -> None:
    valid = (FIXTURES / "rich-v1.json").read_text(encoding="utf-8")
    library = load_physical_text(valid.replace("interop-library", wire_name, 1))
    assert library.name == expected


def test_ci_cross_language_gate_generates_fresh_physical_wire() -> None:
    workflow = (FilePath(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "uv run --frozen python -I examples/physical_library.py" in workflow
    assert "go run ./cmd/physicalir-check" in workflow
    assert "hashlib.sha256(wire.read_bytes()).hexdigest()" in workflow
    assert '"shapes": shape_count' in workflow
    assert "if summary != expected:" in workflow
