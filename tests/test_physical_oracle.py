"""Independent graph and expansion oracles for bounded physical views."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

from spicetrellis.geometry import Point, Rectangle, Transform
from spicetrellis.physical import (
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
    PhysicalShape,
    Technology,
    flatten_layout,
)

_TECHNOLOGY = Technology("oracle", (LayerSpec("wire", 1, 0, LayerPurpose.DRAWING),))


def _library(cells: tuple[PhysicalCell, ...]) -> PhysicalLibrary:
    return PhysicalLibrary("oracle", DistanceUnit.NANOMETER, _TECHNOLOGY, cells)


def _has_cycle(edges: tuple[tuple[int, ...], ...]) -> bool:
    """Classify a directed graph independently with Kahn's algorithm."""
    indegree = [0] * len(edges)
    parents: list[list[int]] = [[] for _ in edges]
    for parent, children in enumerate(edges):
        for child in set(children):
            indegree[parent] += 1
            parents[child].append(parent)
    pending = [index for index, degree in enumerate(indegree) if degree == 0]
    visited = 0
    while pending:
        child = pending.pop()
        visited += 1
        for parent in parents[child]:
            indegree[parent] -= 1
            if indegree[parent] == 0:
                pending.append(parent)
    return visited != len(edges)


def test_seeded_graph_cycle_classification_matches_kahn_oracle() -> None:
    generator = random.Random(0xC1C1E)
    accepted = rejected = 0
    for _ in range(500):
        count = generator.randrange(1, 9)
        edges = tuple(
            tuple(child for child in range(count) if generator.random() < 0.22)
            for _ in range(count)
        )
        cells = tuple(
            PhysicalCell(
                f"c{parent}",
                layout=LayoutView(
                    instances=tuple(
                        PhysicalInstance(f"edge-{slot}", f"c{child}")
                        for slot, child in enumerate(children)
                    )
                ),
            )
            for parent, children in enumerate(edges)
        )
        if _has_cycle(edges):
            rejected += 1
            with pytest.raises(PhysicalError, match="cycle"):
                _library(cells)
        else:
            accepted += 1
            model = _library(cells)
            order = {name: position for position, name in enumerate(model.dependency_order())}
            assert all(
                order[f"c{child}"] < order[f"c{parent}"]
                for parent, row in enumerate(edges)
                for child in row
            )
    assert accepted > 50
    assert rejected > 200


def _expand_paths(
    definitions: tuple[PhysicalCell, ...], name: str, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, tuple[str, ...], str]]:
    """Reference recursive expansion; test graphs are shallow acyclic DAGs."""
    cell = next(cell for cell in definitions if cell.name == name)
    assert cell.layout is not None
    yield "cell", path, name
    for shape in cell.layout.shapes:
        yield "shape", path, shape.net or ""
    for annotation in cell.layout.annotations:
        yield "annotation", path, annotation.text
    for instance in cell.layout.instances:
        yield from _expand_paths(definitions, instance.cell, (*path, instance.name))


def test_seeded_dag_flatten_counts_and_paths_match_recursive_oracle() -> None:
    generator = random.Random(0xF1A77E)
    for case in range(250):
        count = generator.randrange(1, 8)
        cells: list[PhysicalCell] = []
        for parent in range(count):
            shapes = tuple(
                PhysicalShape(
                    "wire",
                    Rectangle(Point(parent * 3 + slot, slot), 1, 1),
                    "shared-net",
                )
                for slot in range(generator.randrange(3))
            )
            annotations = tuple(
                Annotation(f"note-{parent}", Point(parent, -parent))
                for _ in range(generator.randrange(2))
            )
            children = (
                tuple(generator.randrange(parent) for _ in range(generator.randrange(3)))
                if parent
                else ()
            )
            instances = tuple(
                PhysicalInstance(
                    f"case-{case}-slot-{slot}",
                    f"c{child}",
                    Transform(Point(slot - 1, parent), (parent + slot) % 4 * 90, bool(slot % 2)),
                )
                for slot, child in enumerate(children)
            )
            cells.append(
                PhysicalCell(
                    f"c{parent}",
                    layout=LayoutView(shapes=shapes, instances=instances, annotations=annotations),
                )
            )
        definitions = tuple(cells)
        expected = tuple(_expand_paths(definitions, f"c{count - 1}"))
        expected_cells = sum(kind == "cell" for kind, _, _ in expected)
        expected_shapes = tuple((path, value) for kind, path, value in expected if kind == "shape")
        expected_annotations = tuple(
            (path, value) for kind, path, value in expected if kind == "annotation"
        )

        flat = flatten_layout(_library(tuple(reversed(definitions))), f"c{count - 1}")
        assert flat.expanded_cells == expected_cells
        assert (
            tuple((item.instance_path, item.shape.net or "") for item in flat.shapes)
            == expected_shapes
        )
        assert (
            tuple((item.instance_path, item.annotation.text) for item in flat.annotations)
            == expected_annotations
        )


def test_exact_expansion_cost_boundaries_match_recursive_oracle() -> None:
    shape = PhysicalShape("wire", Rectangle(Point(0, 0), 1, 1), "same")
    leaf = PhysicalCell(
        "leaf",
        layout=LayoutView((shape,), annotations=(Annotation("note", Point(0, 0)),)),
    )
    middle = PhysicalCell(
        "middle",
        layout=LayoutView(
            (shape,),
            (PhysicalInstance("left", "leaf"), PhysicalInstance("right", "leaf")),
        ),
    )
    top = PhysicalCell(
        "top",
        layout=LayoutView(instances=(PhysicalInstance("only", "middle"),)),
    )
    model = _library((top, leaf, middle))
    # Recursive expansion is: 4 cells, 3 shapes, 12 stored polygon points,
    # 48 exact intersection-work units, and 2 annotations.
    exact = dict(cells=4, shapes=3, points=12, polygon_work=48, annotations=2)
    flat = flatten_layout(model, "top", limits=FlattenLimits(**exact))
    assert (flat.expanded_cells, len(flat.shapes), len(flat.annotations)) == (4, 3, 2)
    for field, value in exact.items():
        reduced = exact | {field: value - 1}
        with pytest.raises(PhysicalError, match="resource budget"):
            flatten_layout(model, "top", limits=FlattenLimits(**reduced))


def test_raw_library_aggregate_costs_have_exact_inclusive_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import spicetrellis.physical as physical

    shape = PhysicalShape("wire", Rectangle(Point(0, 0), 1, 1), "same")
    cell = PhysicalCell("leaf", layout=LayoutView((shape,)))
    # One shape/item, four materialized points, 4**2 polygon-work units and
    # 28 UTF-8 bytes: library/technology/layer/cell/layer-reference/net text.
    exact = {
        "MAX_ITEMS": 1,
        "MAX_STORED_POINTS": 4,
        "MAX_STORED_POLYGON_WORK": 16,
        "MAX_LIBRARY_TEXT": 28,
    }
    for name, value in exact.items():
        monkeypatch.setattr(physical, name, value)
    assert _library((cell,)).cells == (cell,)

    for name, value in exact.items():
        monkeypatch.setattr(physical, name, value - 1)
        with pytest.raises(PhysicalError, match="aggregate"):
            _library((cell,))
        monkeypatch.setattr(physical, name, value)


def test_instance_paths_are_lossless_and_coincident_shapes_are_not_connected() -> None:
    shape = PhysicalShape("wire", Rectangle(Point(0, 0), 1, 1), "local")
    leaf = PhysicalCell("leaf", layout=LayoutView((shape,)))
    middle = PhysicalCell("middle", layout=LayoutView(instances=(PhysicalInstance("b", "leaf"),)))
    top = PhysicalCell(
        "top",
        layout=LayoutView(
            instances=(
                PhysicalInstance("a/b", "leaf"),
                PhysicalInstance("a", "middle"),
            )
        ),
    )
    flat = flatten_layout(_library((top, middle, leaf)), "top")
    assert tuple(item.instance_path for item in flat.shapes) == (("a/b",), ("a", "b"))
    assert flat.shapes[0].shape == flat.shapes[1].shape
    assert flat.shapes[0] is not flat.shapes[1]
