"""Bounded physical cell libraries, technology maps and hierarchy expansion.

This is geometry interchange, not a layout extractor, DRC engine or placer.
Flattened net labels retain their instance scope: coincident shapes or equal local
labels are never silently promoted to proven electrical connectivity.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

from spicetrellis.geometry import Bounds, Path, Point, Polygon, Rectangle, Transform

MAX_DEFINITIONS = 4_096
MAX_ITEMS = 100_000
MAX_TEXT = 4_096
MAX_DEPTH = 64
MAX_STORED_POINTS = 1_000_000
MAX_STORED_POLYGON_WORK = 10_000_000
MAX_LIBRARY_TEXT = 8 * 1024 * 1024


class PhysicalError(ValueError):
    """An invalid physical library, unsupported view, or exceeded resource budget."""


class DistanceUnit(StrEnum):
    MICROMETER = "um"
    NANOMETER = "nm"
    ANGSTROM = "angstrom"

    @property
    def meters(self) -> Fraction:
        return {
            DistanceUnit.MICROMETER: Fraction(1, 10**6),
            DistanceUnit.NANOMETER: Fraction(1, 10**9),
            DistanceUnit.ANGSTROM: Fraction(1, 10**10),
        }[self]


class LayerPurpose(StrEnum):
    UNKNOWN = "unknown"
    DRAWING = "drawing"
    PIN = "pin"
    LABEL = "label"
    OBSTRUCTION = "obstruction"
    OUTLINE = "outline"


def _name(value: str, field: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_TEXT
        or any(
            ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF
            for char in value
        )
    ):
        raise PhysicalError(f"{field} must be nonempty bounded text without controls or surrogates")


def _items(value: tuple[object, ...], expected: type, field: str, maximum: int = MAX_ITEMS) -> None:
    if type(value) is not tuple or len(value) > maximum:
        raise PhysicalError(f"{field} must be a tuple of at most {maximum} items")
    if any(type(item) is not expected for item in value):
        raise PhysicalError(f"{field} contains an invalid item type")


def _unique(names: tuple[str, ...], field: str) -> None:
    for name in names:
        _name(name, field)
    if len(names) != len(set(names)):
        raise PhysicalError(f"{field} must be unique")


@dataclass(frozen=True, slots=True)
class LayerSpec:
    """One uniquely named numeric layer/purpose pair in a technology."""

    id: str
    number: int
    datatype: int
    purpose: LayerPurpose
    description: str = ""

    def __post_init__(self) -> None:
        _name(self.id, "layer ID")
        for value in (self.number, self.datatype):
            if type(value) is not int or not 0 <= value < 2**64:
                raise PhysicalError("layer number/datatype must be unsigned 64-bit integers")
        if type(self.purpose) is not LayerPurpose:
            raise PhysicalError("layer purpose must be a LayerPurpose")
        if self.description != "":
            _name(self.description, "layer description")


@dataclass(frozen=True, slots=True)
class Technology:
    name: str
    layers: tuple[LayerSpec, ...]
    packages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.name, "technology name")
        _items(self.layers, LayerSpec, "layers", MAX_DEFINITIONS)
        _items(self.packages, str, "packages", MAX_DEFINITIONS)
        _unique(self.packages, "package names")
        _unique(tuple(layer.id for layer in self.layers), "layer IDs")
        if len({(layer.number, layer.datatype) for layer in self.layers}) != len(self.layers):
            raise PhysicalError("numeric layer/purpose pairs must be unique")
        text_size = len(self.name.encode("utf-8"))
        text_size += sum(len(item.encode("utf-8")) for item in self.packages)
        text_size += sum(
            len((layer.id + layer.description).encode("utf-8")) for layer in self.layers
        )
        if text_size > MAX_LIBRARY_TEXT:
            raise PhysicalError("technology exceeds aggregate text budget")


@dataclass(frozen=True, slots=True)
class PhysicalShape:
    layer: str
    geometry: Rectangle | Polygon | Path
    net: str | None = None

    def __post_init__(self) -> None:
        _name(self.layer, "shape layer")
        if type(self.geometry) not in (Rectangle, Polygon, Path):
            raise PhysicalError("shape geometry must be a Rectangle, Polygon or Path")
        if self.net is not None:
            _name(self.net, "local net label")


@dataclass(frozen=True, slots=True)
class Annotation:
    text: str
    at: Point

    def __post_init__(self) -> None:
        _name(self.text, "annotation")
        if type(self.at) is not Point:
            raise PhysicalError("annotation location must be a Point")


@dataclass(frozen=True, slots=True)
class PhysicalPort:
    name: str
    shapes: tuple[PhysicalShape, ...]

    def __post_init__(self) -> None:
        _name(self.name, "physical port")
        _items(self.shapes, PhysicalShape, "port shapes")
        if not self.shapes:
            raise PhysicalError("physical ports require at least one shape")
        if any(shape.net is not None and shape.net != self.name for shape in self.shapes):
            raise PhysicalError("physical port shape labels must match the port name")


@dataclass(frozen=True, slots=True)
class AbstractView:
    """An outline, access shapes and blockage geometry; no implicit DRC claim."""

    outline: Polygon
    ports: tuple[PhysicalPort, ...] = ()
    blockages: tuple[PhysicalShape, ...] = ()

    def __post_init__(self) -> None:
        if type(self.outline) is not Polygon:
            raise PhysicalError("abstract outline must be a Polygon")
        _items(self.ports, PhysicalPort, "abstract ports")
        _items(self.blockages, PhysicalShape, "abstract blockages")
        _unique(tuple(port.name for port in self.ports), "abstract port names")


@dataclass(frozen=True, slots=True)
class PhysicalInstance:
    name: str
    cell: str
    transform: Transform = field(default_factory=Transform)

    def __post_init__(self) -> None:
        _name(self.name, "instance name")
        _name(self.cell, "instance cell")
        if type(self.transform) is not Transform:
            raise PhysicalError("instance placement must be a Transform")


@dataclass(frozen=True, slots=True)
class LayoutView:
    shapes: tuple[PhysicalShape, ...] = ()
    instances: tuple[PhysicalInstance, ...] = ()
    annotations: tuple[Annotation, ...] = ()

    def __post_init__(self) -> None:
        _items(self.shapes, PhysicalShape, "layout shapes")
        _items(self.instances, PhysicalInstance, "layout instances")
        _items(self.annotations, Annotation, "layout annotations")
        _unique(tuple(instance.name for instance in self.instances), "instance names")


@dataclass(frozen=True, slots=True)
class PhysicalCell:
    name: str
    ports: tuple[str, ...] = ()
    layout: LayoutView | None = None
    abstract: AbstractView | None = None
    circuit_module: str | None = None

    def __post_init__(self) -> None:
        _name(self.name, "cell name")
        _items(self.ports, str, "cell ports")
        _unique(self.ports, "cell ports")
        if self.layout is not None and type(self.layout) is not LayoutView:
            raise PhysicalError("layout view must be a LayoutView")
        if self.abstract is not None and type(self.abstract) is not AbstractView:
            raise PhysicalError("abstract view must be an AbstractView")
        if self.circuit_module is not None:
            _name(self.circuit_module, "circuit module reference")
        if self.layout is None and self.abstract is None and self.circuit_module is None:
            raise PhysicalError("a physical cell requires at least one view")
        if self.abstract is not None:
            declared = set(self.ports)
            if any(port.name not in declared for port in self.abstract.ports):
                raise PhysicalError("abstract ports must belong to the cell interface")


def _cell_shapes(cell: PhysicalCell) -> Iterator[PhysicalShape]:
    if cell.layout is not None:
        yield from cell.layout.shapes
    if cell.abstract is not None:
        yield from cell.abstract.blockages
        for port in cell.abstract.ports:
            yield from port.shapes


@dataclass(frozen=True, slots=True)
class PhysicalLibrary:
    name: str
    unit: DistanceUnit
    technology: Technology
    cells: tuple[PhysicalCell, ...]

    def __post_init__(self) -> None:
        _name(self.name, "library name")
        if type(self.unit) is not DistanceUnit or type(self.technology) is not Technology:
            raise PhysicalError("library requires a DistanceUnit and Technology")
        _items(self.cells, PhysicalCell, "cells", MAX_DEFINITIONS)
        _unique(tuple(cell.name for cell in self.cells), "cell names")
        layers = {layer.id for layer in self.technology.layers}
        cells = {cell.name: cell for cell in self.cells}
        count = 0
        points = 0
        polygon_work = 0
        text_size = len(self.name.encode("utf-8")) + len(self.technology.name.encode("utf-8"))
        text_size += sum(len(name.encode("utf-8")) for name in self.technology.packages)
        text_size += sum(
            len((layer.id + layer.description).encode("utf-8")) for layer in self.technology.layers
        )
        if text_size > MAX_LIBRARY_TEXT:
            raise PhysicalError("library exceeds aggregate text budget")
        for cell in self.cells:
            count += len(cell.ports)
            text_size += len((cell.name + (cell.circuit_module or "")).encode("utf-8"))
            text_size += sum(len(port.encode("utf-8")) for port in cell.ports)
            if cell.layout is not None:
                count += len(cell.layout.instances) + len(cell.layout.annotations)
                text_size += sum(
                    len((inst.name + inst.cell).encode("utf-8")) for inst in cell.layout.instances
                )
                text_size += sum(len(item.text.encode("utf-8")) for item in cell.layout.annotations)
                points += len(cell.layout.annotations)
            if cell.abstract is not None:
                points += len(cell.abstract.outline.vertices)
                polygon_work += len(cell.abstract.outline.vertices) ** 2
                count += len(cell.abstract.ports)
                text_size += sum(len(port.name.encode("utf-8")) for port in cell.abstract.ports)
            for shape in _cell_shapes(cell):
                count += 1
                shape_points, shape_work = _shape_cost(shape)
                points += shape_points
                polygon_work += shape_work
                text_size += len((shape.layer + (shape.net or "")).encode("utf-8"))
                if (
                    count > MAX_ITEMS
                    or points > MAX_STORED_POINTS
                    or text_size > MAX_LIBRARY_TEXT
                    or polygon_work > MAX_STORED_POLYGON_WORK
                ):
                    raise PhysicalError("library exceeds aggregate stored item/point/text budget")
                if shape.layer not in layers:
                    raise PhysicalError("shape references an undeclared technology layer")
            if (
                count > MAX_ITEMS
                or points > MAX_STORED_POINTS
                or text_size > MAX_LIBRARY_TEXT
                or polygon_work > MAX_STORED_POLYGON_WORK
            ):
                raise PhysicalError("library exceeds aggregate stored item budget")
            if cell.layout is not None and any(
                inst.cell not in cells for inst in cell.layout.instances
            ):
                raise PhysicalError("layout instance references an undefined cell")
        self.dependency_order()

    def dependency_order(self) -> tuple[str, ...]:
        """Children before parents, with iterative cycle and longest-path checks."""
        cells = {cell.name: cell for cell in self.cells}
        complete: set[str] = set()
        depth: dict[str, int] = {}
        ordered: list[str] = []
        for root in cells:
            if root in complete:
                continue
            active: set[str] = set()
            stack = [(root, False)]
            while stack:
                name, exiting = stack.pop()
                if name in complete:
                    continue
                cell = cells[name]
                children = cell.layout.instances if cell.layout is not None else ()
                if exiting:
                    depth[name] = 1 + max((depth[child.cell] for child in children), default=0)
                    if depth[name] > MAX_DEPTH:
                        raise PhysicalError("physical hierarchy exceeds depth 64")
                    active.remove(name)
                    complete.add(name)
                    ordered.append(name)
                else:
                    if name in active:
                        raise PhysicalError("physical hierarchy contains a cycle")
                    active.add(name)
                    if len(active) > MAX_DEPTH:
                        raise PhysicalError("physical hierarchy exceeds depth 64")
                    stack.append((name, True))
                    stack.extend((child.cell, False) for child in reversed(children))
        return tuple(ordered)


@dataclass(frozen=True, slots=True)
class FlattenLimits:
    cells: int = 100_000
    shapes: int = 100_000
    points: int = 1_000_000
    polygon_work: int = 10_000_000
    annotations: int = 100_000

    def __post_init__(self) -> None:
        for value, maximum in zip(
            (self.cells, self.shapes, self.points, self.polygon_work, self.annotations),
            (1_000_000, 1_000_000, 4_000_000, 100_000_000, 1_000_000),
            strict=True,
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise PhysicalError(f"flatten limit must be an integer in 1..{maximum}")


_DEFAULT_LIMITS = FlattenLimits()


@dataclass(frozen=True, slots=True)
class PlacedShape:
    instance_path: tuple[str, ...]
    shape: PhysicalShape


@dataclass(frozen=True, slots=True)
class PlacedAnnotation:
    instance_path: tuple[str, ...]
    annotation: Annotation


@dataclass(frozen=True, slots=True)
class FlatLayout:
    top: str
    unit: DistanceUnit
    expanded_cells: int
    shapes: tuple[PlacedShape, ...]
    annotations: tuple[PlacedAnnotation, ...]

    @property
    def bounds(self) -> Bounds | None:
        result: Bounds | None = None
        for item in self.shapes:
            bound = item.shape.geometry.bounds
            result = bound if result is None else result.union(bound)
        return result


def _shape_cost(shape: PhysicalShape) -> tuple[int, int]:
    if type(shape.geometry) is Rectangle:
        return 4, 16
    if type(shape.geometry) is Polygon:
        size = len(shape.geometry.vertices)
        return size, size * size
    if type(shape.geometry) is Path:
        return len(shape.geometry.points), 0
    raise PhysicalError("unsupported geometry")


def flatten_layout(
    library: PhysicalLibrary, top: str, *, limits: FlattenLimits = _DEFAULT_LIMITS
) -> FlatLayout:
    """Materialize layout geometry only after whole-expansion cost preflight.

    Abstract-only cells are not empty implementations. An overflow or unsupported
    view fails the whole operation; no truncated/partial layout is returned.
    """
    if type(library) is not PhysicalLibrary or type(limits) is not FlattenLimits:
        raise PhysicalError("flatten requires a PhysicalLibrary and FlattenLimits")
    _name(top, "top cell")
    cells = {cell.name: cell for cell in library.cells}
    if top not in cells:
        raise PhysicalError("top cell does not exist")
    needed: set[str] = set()
    pending = [top]
    while pending:
        name = pending.pop()
        if name in needed:
            continue
        needed.add(name)
        view = cells[name].layout
        if view is None:
            raise PhysicalError(f"cell {name!r} has no materializable layout view")
        pending.extend(instance.cell for instance in view.instances)

    costs: dict[str, tuple[int, int, int, int, int]] = {}
    ceilings = (limits.cells, limits.shapes, limits.points, limits.polygon_work, limits.annotations)
    for name in library.dependency_order():
        if name not in needed:
            continue
        view = cells[name].layout
        if view is None:
            raise PhysicalError("required layout view disappeared")
        shape_costs = tuple(_shape_cost(shape) for shape in view.shapes)
        total = [
            1,
            len(view.shapes),
            sum(c[0] for c in shape_costs),
            sum(c[1] for c in shape_costs),
            len(view.annotations),
        ]
        for instance in view.instances:
            child = costs[instance.cell]
            total = [min(a + b, cap + 1) for a, b, cap in zip(total, child, ceilings, strict=True)]
        if any(value > cap for value, cap in zip(total, ceilings, strict=True)):
            raise PhysicalError(f"expanded physical layout exceeds resource budget at {name!r}")
        costs[name] = (total[0], total[1], total[2], total[3], total[4])

    shapes: list[PlacedShape] = []
    annotations: list[PlacedAnnotation] = []
    stack: list[tuple[str, Transform, tuple[str, ...]]] = [(top, Transform(), ())]
    while stack:
        name, transform, path = stack.pop()
        view = cells[name].layout
        if view is None:
            raise PhysicalError("required layout view disappeared")
        for shape in view.shapes:
            shapes.append(
                PlacedShape(
                    path, PhysicalShape(shape.layer, transform.shape(shape.geometry), shape.net)
                )
            )
        for annotation in view.annotations:
            annotations.append(
                PlacedAnnotation(path, Annotation(annotation.text, transform.apply(annotation.at)))
            )
        for instance in reversed(view.instances):
            stack.append(
                (instance.cell, transform.compose(instance.transform), (*path, instance.name))
            )
    return FlatLayout(top, library.unit, costs[top][0], tuple(shapes), tuple(annotations))
