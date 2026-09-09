"""Physical-view contracts, hierarchy behavior and pre-expansion resource gates."""

from dataclasses import FrozenInstanceError
from fractions import Fraction

import pytest

from spicetrellis.geometry import (
    INT64_MAX,
    Bounds,
    GeometryError,
    Path,
    Point,
    Polygon,
    Rectangle,
    Transform,
)
from spicetrellis.physical import (
    AbstractView,
    Annotation,
    DistanceUnit,
    FlattenLimits,
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

TECH = Technology(
    "original-demo",
    (
        LayerSpec("m1.draw", 1, 0, LayerPurpose.DRAWING),
        LayerSpec("m1.pin", 1, 1, LayerPurpose.PIN),
    ),
    ("demo-cells",),
)
BOX = Rectangle(Point(0, 0), 2, 4)
SHAPE = PhysicalShape("m1.draw", BOX, "local")


def library(*cells):
    return PhysicalLibrary("demo", DistanceUnit.NANOMETER, TECH, cells)


def test_units_are_exact_and_technology_layers_are_unambiguous():
    assert DistanceUnit.MICROMETER.meters == Fraction(1, 10**6)
    assert DistanceUnit.NANOMETER.meters == Fraction(1, 10**9)
    assert DistanceUnit.ANGSTROM.meters == Fraction(1, 10**10)
    assert TECH.layers[0].purpose is LayerPurpose.DRAWING
    with pytest.raises(FrozenInstanceError):
        TECH.name = "changed"


@pytest.mark.parametrize("name", [None, "", "x\n", "\ud800", "a" * 4097, "x\x7f", 1])
def test_invalid_names_fail_as_domain_errors(name):
    with pytest.raises(PhysicalError):
        Technology(name, ())


@pytest.mark.parametrize("number,datatype", [(True, 0), (-1, 0), (2**64, 0), (0, 1.0), (0, -1)])
def test_layer_numbers_are_uint64(number, datatype):
    with pytest.raises(PhysicalError):
        LayerSpec("m1", number, datatype, LayerPurpose.UNKNOWN)


def test_layer_and_technology_structural_validation():
    assert LayerSpec("largest", 2**64 - 1, 2**64 - 1, LayerPurpose.UNKNOWN, "unmapped")
    for make in (
        lambda: LayerSpec("m1", 1, 0, "drawing"),
        lambda: LayerSpec("m1", 1, 0, LayerPurpose.DRAWING, False),
        lambda: Technology("t", []),
        lambda: Technology("t", ("layer",)),
        lambda: Technology("t", (TECH.layers[0], TECH.layers[0])),
        lambda: Technology("t", (TECH.layers[0], LayerSpec("alias", 1, 0, LayerPurpose.PIN))),
        lambda: Technology("t", (), ("p", "p")),
        lambda: Technology("t", (), ["p"]),
        lambda: Technology("t", TECH.layers * 4096),
    ):
        with pytest.raises(PhysicalError):
            make()


def test_shape_port_and_abstract_validation():
    pin = PhysicalShape("m1.pin", BOX, "p")
    port = PhysicalPort("p", (pin,))
    abstract = AbstractView(BOX.polygon(), (port,), (SHAPE,))
    cell = PhysicalCell("leaf", ("p",), abstract=abstract)
    assert library(cell).cells[0].abstract == abstract
    for make in (
        lambda: PhysicalShape("m1", None),
        lambda: PhysicalShape("m1", BOX, ""),
        lambda: Annotation("note", (0, 0)),
        lambda: PhysicalPort("p", ()),
        lambda: PhysicalPort("p", (SHAPE,)),
        lambda: AbstractView(BOX),
        lambda: AbstractView(BOX.polygon(), (port, port)),
        lambda: PhysicalInstance("x", "leaf", None),
        lambda: LayoutView(instances=(PhysicalInstance("x", "leaf"),) * 2),
        lambda: PhysicalCell("empty"),
        lambda: PhysicalCell("bad", layout="layout"),
        lambda: PhysicalCell("bad", abstract="abstract"),
        lambda: PhysicalCell("bad", circuit_module=""),
        lambda: PhysicalCell("bad", (), abstract=abstract),
    ):
        with pytest.raises(PhysicalError):
            make()


def test_library_detects_invalid_layers_references_and_cycles():
    for make in (
        lambda: PhysicalLibrary("x", "nm", TECH, ()),
        lambda: PhysicalLibrary("x", DistanceUnit.NANOMETER, None, ()),
        lambda: library(
            PhysicalCell("a", layout=LayoutView()), PhysicalCell("a", layout=LayoutView())
        ),
        lambda: library(PhysicalCell("a", layout=LayoutView((PhysicalShape("missing", BOX),)))),
        lambda: library(
            PhysicalCell("a", layout=LayoutView(instances=(PhysicalInstance("x", "absent"),)))
        ),
        lambda: library(
            PhysicalCell("a", layout=LayoutView(instances=(PhysicalInstance("x", "a"),)))
        ),
        lambda: library(
            PhysicalCell("a", layout=LayoutView(instances=(PhysicalInstance("x", "b"),))),
            PhysicalCell("b", layout=LayoutView(instances=(PhysicalInstance("x", "a"),))),
        ),
    ):
        with pytest.raises(PhysicalError):
            make()


def test_diamond_dag_in_any_definition_order_is_not_a_cycle():
    leaf = PhysicalCell("leaf", layout=LayoutView((SHAPE,)))
    a = PhysicalCell("a", layout=LayoutView(instances=(PhysicalInstance("x", "leaf"),)))
    b = PhysicalCell("b", layout=LayoutView(instances=(PhysicalInstance("y", "leaf"),)))
    root = PhysicalCell(
        "root",
        layout=LayoutView(instances=(PhysicalInstance("a", "a"), PhysicalInstance("b", "b"))),
    )
    for cells in ((root, a, b, leaf), (leaf, b, a, root)):
        model = library(*cells)
        order = model.dependency_order()
        assert order.index("leaf") < order.index("a") < order.index("root")
        assert order.index("leaf") < order.index("b") < order.index("root")
        flat = flatten_layout(model, "root")
        assert flat.expanded_cells == 5
        assert tuple(item.instance_path for item in flat.shapes) == (("a", "x"), ("b", "y"))
        # Same local labels stay scoped; this is not an electrical union.
        assert tuple(item.shape.net for item in flat.shapes) == ("local", "local")


def chain(count):
    return tuple(
        PhysicalCell(
            str(index),
            layout=LayoutView(instances=(PhysicalInstance("x", str(index - 1)),) if index else ()),
        )
        for index in range(count)
    )


def test_longest_depth_is_bounded_even_when_children_were_already_visited():
    assert len(library(*chain(64)).dependency_order()) == 64
    assert len(library(*reversed(chain(64))).dependency_order()) == 64
    for cells in (chain(65), tuple(reversed(chain(65)))):
        with pytest.raises(PhysicalError, match="depth 64"):
            library(*cells)


def test_nested_transforms_annotations_and_flat_bounds():
    leaf = PhysicalCell(
        "leaf", layout=LayoutView((SHAPE,), annotations=(Annotation("note", Point(1, 2)),))
    )
    middle = PhysicalCell(
        "middle",
        layout=LayoutView(
            instances=(PhysicalInstance("i", "leaf", Transform(Point(3, 7), 90, True)),)
        ),
    )
    root = PhysicalCell(
        "root",
        layout=LayoutView(
            instances=(PhysicalInstance("j", "middle", Transform(Point(-2, 4), 270)),)
        ),
    )
    model = library(root, middle, leaf)
    flat = flatten_layout(model, "root")
    combined = Transform(Point(-2, 4), 270).compose(Transform(Point(3, 7), 90, True))
    assert flat.shapes[0].shape.geometry == combined.shape(BOX)
    assert flat.annotations[0].annotation.at == combined.apply(Point(1, 2))
    assert flat.annotations[0].instance_path == ("j", "i")
    assert flat.bounds == combined.shape(BOX).bounds
    assert (
        flatten_layout(library(PhysicalCell("empty", layout=LayoutView())), "empty").bounds is None
    )


def test_flat_bounds_union_path_and_polygon():
    shapes = (
        SHAPE,
        PhysicalShape("m1.draw", Path((Point(7, 3), Point(7, 8)), 1)),
        PhysicalShape("m1.draw", Polygon((Point(-2, 0), Point(-1, 0), Point(-2, 1)))),
    )
    flat = flatten_layout(library(PhysicalCell("top", layout=LayoutView(shapes))), "top")
    assert flat.bounds == Bounds(-2, 0, Fraction(15, 2), Fraction(17, 2))


def test_abstract_only_and_circuit_only_are_not_empty_layouts():
    for cell in (
        PhysicalCell("a", abstract=AbstractView(BOX.polygon())),
        PhysicalCell("a", circuit_module="circuit/a"),
    ):
        model = library(cell)
        with pytest.raises(PhysicalError, match="no materializable"):
            flatten_layout(model, "a")
    for args in ((None, "a"), (library(), "a"), (library(), None)):
        with pytest.raises(PhysicalError):
            flatten_layout(*args)
    with pytest.raises(PhysicalError):
        flatten_layout(library(), "a", limits=None)


def test_unused_abstract_view_does_not_block_materializable_root():
    model = library(
        PhysicalCell("abstract", abstract=AbstractView(BOX.polygon())),
        PhysicalCell("top", layout=LayoutView()),
    )
    assert flatten_layout(model, "top").expanded_cells == 1


@pytest.mark.parametrize(
    "options",
    [
        {"cells": 0},
        {"shapes": True},
        {"points": 4_000_001},
        {"polygon_work": 100_000_001},
        {"annotations": 1.5},
    ],
)
def test_invalid_budget(options):
    with pytest.raises(PhysicalError):
        FlattenLimits(**options)


@pytest.mark.parametrize(
    "options",
    [{"cells": 1}, {"shapes": 1}, {"points": 7}, {"polygon_work": 31}, {"annotations": 1}],
)
def test_cost_limits_fail_before_materializing_any_geometry(monkeypatch, options):
    leaf = PhysicalCell(
        "leaf", layout=LayoutView((SHAPE,), annotations=(Annotation("n", Point(0, 0)),))
    )
    top = PhysicalCell(
        "top",
        layout=LayoutView(instances=(PhysicalInstance("a", "leaf"), PhysicalInstance("b", "leaf"))),
    )
    model = library(top, leaf)

    def fail(*_args):
        pytest.fail("geometry materialized before complete budget preflight")

    monkeypatch.setattr(Transform, "shape", fail)
    with pytest.raises(PhysicalError, match="resource budget"):
        flatten_layout(model, "top", limits=FlattenLimits(**options))


def test_doubling_hierarchy_is_rejected_without_expansion(monkeypatch):
    cells = [PhysicalCell("0", layout=LayoutView((SHAPE,)))]
    for index in range(1, 64):
        cells.append(
            PhysicalCell(
                str(index),
                layout=LayoutView(
                    instances=(
                        PhysicalInstance("a", str(index - 1)),
                        PhysicalInstance("b", str(index - 1)),
                    )
                ),
            )
        )
    model = library(*cells)
    with pytest.raises(PhysicalError, match="resource budget"):
        flatten_layout(model, "63")


def test_transform_overflow_never_returns_partial_geometry():
    leaf = PhysicalCell("leaf", layout=LayoutView((SHAPE,)))
    top = PhysicalCell(
        "top",
        layout=LayoutView(
            (SHAPE,), (PhysicalInstance("x", "leaf", Transform(Point(INT64_MAX, 0))),)
        ),
    )
    with pytest.raises(GeometryError, match="signed 64-bit"):
        flatten_layout(library(top, leaf), "top")


def test_library_aggregate_budgets_apply_before_unbounded_collection(monkeypatch):
    import spicetrellis.physical as physical

    cell = PhysicalCell("top", layout=LayoutView((SHAPE, SHAPE)))
    monkeypatch.setattr(physical, "MAX_ITEMS", 1)
    with pytest.raises(PhysicalError, match="aggregate"):
        library(cell)
    monkeypatch.setattr(physical, "MAX_ITEMS", 100_000)
    monkeypatch.setattr(physical, "MAX_STORED_POINTS", 7)
    with pytest.raises(PhysicalError, match="aggregate"):
        library(cell)
    monkeypatch.setattr(physical, "MAX_STORED_POINTS", 1_000_000)
    monkeypatch.setattr(physical, "MAX_LIBRARY_TEXT", 1)
    with pytest.raises(PhysicalError, match="aggregate"):
        library()
    with pytest.raises(PhysicalError, match="aggregate"):
        Technology("name", ())


def test_aggregate_budget_includes_abstract_ports_and_annotation_only_cells(monkeypatch):
    import spicetrellis.physical as physical

    port = PhysicalPort("p", (PhysicalShape("m1.pin", BOX, "p"),))
    cell = PhysicalCell("abstract", ("p",), abstract=AbstractView(BOX.polygon(), (port,), (SHAPE,)))
    monkeypatch.setattr(physical, "MAX_ITEMS", 2)
    with pytest.raises(PhysicalError, match="aggregate"):
        library(cell)
    annotation_only = PhysicalCell(
        "notes", layout=LayoutView(annotations=(Annotation("x", Point(0, 0)),) * 3)
    )
    with pytest.raises(PhysicalError, match="aggregate"):
        library(annotation_only)
