"""Lossless physical v1 interchange and fail-closed decoding."""

import json
from copy import deepcopy

import pytest

from spicetrellis.geometry import INT64_MAX, INT64_MIN, Path, Point, Polygon, Rectangle, Transform
from spicetrellis.physical import (
    AbstractView,
    Annotation,
    DistanceUnit,
    LayerPurpose,
    LayerSpec,
    LayoutView,
    PhysicalCell,
    PhysicalError,
    PhysicalInstance,
    PhysicalLibrary,
    PhysicalPort,
    PhysicalShape,
    Technology,
    flatten_layout,
)
from spicetrellis.physical_json import (
    dump_physical,
    load_physical,
    load_physical_text,
    physical_data,
    physical_digest,
)


def rich_library():
    technology = Technology(
        "工艺-demo",
        (
            LayerSpec(
                "m1", 2**64 - 1, 2**64 - 1, LayerPurpose.DRAWING, "original geometry example"
            ),
            LayerSpec("pin", 1, 1, LayerPurpose.PIN),
        ),
        ("primitive", "custom"),
    )
    box = Rectangle(Point(-3, -4), 4, 8)
    outline = Rectangle(Point(-5, -5), 10, 12).polygon()
    pin = PhysicalShape("pin", Rectangle(Point(-3, -4), 1, 1), "p")
    layout = LayoutView(
        (
            PhysicalShape("m1", box, "p"),
            PhysicalShape("m1", Polygon((Point(0, 0), Point(2, 0), Point(0, 2)))),
            PhysicalShape("m1", Path((Point(2, 3), Point(3, 4)), 1)),
        ),
        annotations=(Annotation("label ≠ connectivity", Point(0, 0)),),
    )
    leaf = PhysicalCell(
        "leaf",
        ("p",),
        layout,
        AbstractView(outline, (PhysicalPort("p", (pin,)),), (PhysicalShape("m1", box),)),
        "circuit/leaf",
    )
    top = PhysicalCell(
        "top",
        layout=LayoutView(
            instances=(PhysicalInstance("mirror", "leaf", Transform(Point(20, 30), 90, True)),)
        ),
    )
    return PhysicalLibrary("test-library", DistanceUnit.NANOMETER, technology, (top, leaf))


def test_rich_raw_layout_round_trip_is_exact_and_deterministic():
    model = rich_library()
    text = dump_physical(model)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "工艺-demo" in text
    assert '"number":"18446744073709551615"' in text
    assert load_physical_text(text) == model
    assert dump_physical(load_physical_text(text)) == text
    assert flatten_layout(load_physical_text(text), "top") == flatten_layout(model, "top")
    assert physical_digest(model) == physical_digest(load_physical_text(text))
    assert len(physical_digest(model)) == 64


def test_signed64_coordinates_are_strings_not_lossy_json_numbers():
    model = PhysicalLibrary(
        "extremes",
        DistanceUnit.ANGSTROM,
        Technology("t", ()),
        (
            PhysicalCell(
                "points",
                layout=LayoutView(annotations=(Annotation("minmax", Point(INT64_MIN, INT64_MAX)),)),
            ),
        ),
    )
    data = physical_data(model)
    assert data["cells"][0]["layout"]["annotations"][0]["at"] == [
        "-9223372036854775808",
        "9223372036854775807",
    ]
    assert load_physical_text(dump_physical(model)) == model


def test_views_can_be_independently_absent_and_payload_is_detached():
    model = PhysicalLibrary(
        "only-circuit",
        DistanceUnit.MICROMETER,
        Technology("t", ()),
        (PhysicalCell("logical", circuit_module="m"),),
    )
    assert load_physical_text(dump_physical(model)) == model
    data = physical_data(model)
    data["cells"][0]["name"] = "changed"
    assert model.cells[0].name == "logical"
    with pytest.raises(PhysicalError):
        physical_data(None)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        "[]",
        "null",
        "{",
        '{"x":1,"x":2}',
        '{"x":{"y":1,"y":2}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1.0}',
        '{"x":1e0}',
        "[" * 1100 + "]" * 1100,
        '"\ud800"',
        '{"x":' + "9" * 5000 + "}",
    ],
)
def test_malformed_json_is_a_typed_error(text):
    with pytest.raises(PhysicalError):
        load_physical_text(text)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown"),
        ("version", True),
        ("version", 2),
        ("version", "1"),
        ("name", None),
        ("name", ""),
        ("name", "x" * 4097),
        ("unit", "meter"),
        ("unit", 1),
        ("cells", {}),
        ("technology", []),
        ("extra", "unknown"),
    ],
)
def test_top_contract_rejects_unknown_and_mistyped_fields(field, value):
    data = physical_data(rich_library())
    data[field] = value
    with pytest.raises(PhysicalError):
        load_physical_text(json.dumps(data))


@pytest.mark.parametrize(
    "value",
    [
        0,
        0.0,
        True,
        None,
        "00",
        "-0",
        "+1",
        " 1",
        "1 ",
        "1.0",
        "1e0",
        "\u0661",
        "1" * 21,
        "18446744073709551616",
        "-1",
    ],
)
def test_layer_uint64_wire_is_strict_and_canonical(value):
    data = physical_data(rich_library())
    data["technology"]["layers"][0]["number"] = value
    with pytest.raises(PhysicalError):
        load_physical_text(json.dumps(data))


@pytest.mark.parametrize(
    "value", ["9223372036854775808", "-9223372036854775809", "-0", 1, None, "", "1\n"]
)
def test_point_fields_reject_integer_overflow_and_noncanonical_text(value):
    data = physical_data(rich_library())
    data["cells"][0]["layout"]["instances"][0]["transform"]["origin"][0] = value
    with pytest.raises(PhysicalError):
        load_physical_text(json.dumps(data))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["technology"]["layers"][0].update(purpose="conducting"),
        lambda d: d["technology"].update(packages=[1]),
        lambda d: d["cells"][1].update(ports=[False]),
        lambda d: d["cells"][0]["layout"]["instances"][0].update(transform={}),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(clockwise=45),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(clockwise=True),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(reflect_x=0),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(origin=["0"]),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(
            origin=["0", "0", "0"]
        ),
        lambda d: d["cells"][0]["layout"]["instances"][0]["transform"].update(origin=None),
        lambda d: d["cells"][1]["layout"]["shapes"][0].update(geometry=None),
        lambda d: d["cells"][1]["layout"]["shapes"][0]["geometry"].update(kind="circle"),
        lambda d: d["cells"][1]["layout"]["shapes"][0]["geometry"].update(size=[]),
        lambda d: d["cells"][1]["layout"]["shapes"][0]["geometry"].update(size=["0", "1"]),
        lambda d: d["cells"][1]["layout"]["shapes"][0].update(layer="undeclared"),
        lambda d: d["cells"][1]["abstract"].update(
            outline={"kind": "rectangle", "origin": ["0", "0"], "size": ["1", "1"]}
        ),
        lambda d: d["cells"][1]["abstract"]["ports"][0].update(shapes=[]),
        lambda d: d["cells"][1]["abstract"]["ports"][0].update(name="not-on-interface"),
        lambda d: d["cells"][1]["layout"]["annotations"][0].update(text="\u0000"),
    ],
)
def test_nested_contract_errors_do_not_silently_drop_data(mutation):
    data = physical_data(rich_library())
    mutation(data)
    with pytest.raises(PhysicalError):
        load_physical_text(json.dumps(data))


def test_duplicate_names_and_hierarchy_cycle_are_not_normalized_away():
    data = physical_data(rich_library())
    data["cells"].append(deepcopy(data["cells"][0]))
    with pytest.raises(PhysicalError, match="unique"):
        load_physical_text(json.dumps(data))
    data = physical_data(rich_library())
    data["cells"][0]["layout"]["instances"][0]["cell"] = "top"
    with pytest.raises(PhysicalError, match="cycle"):
        load_physical_text(json.dumps(data))


def test_wire_size_budget_counts_utf8_bytes_and_streamed_output(monkeypatch):
    import spicetrellis.physical_json as wire

    monkeypatch.setattr(wire, "MAX_PHYSICAL_BYTES", 16)
    with pytest.raises(PhysicalError, match="bounded text"):
        load_physical_text(" " * 17)
    with pytest.raises(PhysicalError, match="byte budget"):
        load_physical_text("工" * 6)
    with pytest.raises(PhysicalError, match="byte budget"):
        dump_physical(rich_library())
    with pytest.raises(PhysicalError):
        load_physical_text(b"{}")


def test_aggregate_decoder_budget_precedes_expensive_polygon_validation(monkeypatch):
    import spicetrellis.physical_json as wire

    data = physical_data(rich_library())
    monkeypatch.setattr(wire, "MAX_STORED_POLYGON_WORK", 1)

    def forbidden(*_args, **_kwargs):
        pytest.fail("constructed a polygon before checking its work budget")

    monkeypatch.setattr(wire, "Polygon", forbidden)
    with pytest.raises(PhysicalError, match="aggregate"):
        load_physical_text(json.dumps(data))


def test_stored_item_and_point_budgets_are_not_expansion_estimates(monkeypatch):
    import spicetrellis.physical_json as wire

    text = dump_physical(rich_library())
    monkeypatch.setattr(wire, "MAX_ITEMS", 1)
    with pytest.raises(PhysicalError, match="aggregate"):
        load_physical_text(text)
    monkeypatch.setattr(wire, "MAX_ITEMS", 100_000)
    monkeypatch.setattr(wire, "MAX_STORED_POINTS", 1)
    with pytest.raises(PhysicalError, match="aggregate"):
        load_physical_text(text)


def test_file_reader_is_bounded_and_reports_io_and_encoding_failures(tmp_path, monkeypatch):
    import spicetrellis.physical_json as wire

    path = tmp_path / "physical.json"
    path.write_text(dump_physical(rich_library()), encoding="utf-8")
    assert load_physical(path) == rich_library()
    assert load_physical(str(path)) == rich_library()
    with pytest.raises(PhysicalError, match="cannot read"):
        load_physical(tmp_path / "missing")
    path.write_bytes(b"\xff")
    with pytest.raises(PhysicalError, match="cannot read"):
        load_physical(path)
    path.write_bytes(b" " * 17)
    monkeypatch.setattr(wire, "MAX_PHYSICAL_BYTES", 16)
    with pytest.raises(PhysicalError, match="file exceeds"):
        load_physical(path)


@pytest.mark.parametrize("path", [None, 1, True, "bad\x00path"])
def test_file_reader_misuse_has_a_domain_error(path):
    with pytest.raises(PhysicalError, match="cannot read"):
        load_physical(path)


def test_canonical_stream_matches_standard_json_on_detached_data():
    model = rich_library()
    assert (
        dump_physical(model)
        == json.dumps(
            physical_data(model), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    )


def test_physical_writer_preserves_existing_and_protected_files(tmp_path):
    from spicetrellis import write_physical

    destination = tmp_path / "new.json"
    write_physical(rich_library(), destination)
    original = destination.read_bytes()
    with pytest.raises(PhysicalError, match="cannot write"):
        write_physical(rich_library(), destination)
    write_physical(rich_library(), destination, force=True)
    with pytest.raises(PhysicalError, match="cannot write"):
        write_physical(rich_library(), destination, force=True, protected=(destination,))
    assert destination.read_bytes() == original
