"""Strict physical-library JSON v1, with lossless decimal-string 64-bit fields.

JSON consumers must not need binary64 integers to preserve geometry. Coordinates,
sizes and numeric layer pairs are canonical decimal strings, including zero.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import NoReturn

from spicetrellis._output import write_text_atomic
from spicetrellis.geometry import GeometryError, Path, Point, Polygon, Rectangle, Transform
from spicetrellis.physical import (
    MAX_DEFINITIONS,
    MAX_ITEMS,
    MAX_STORED_POINTS,
    MAX_STORED_POLYGON_WORK,
    MAX_TEXT,
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
)

PHYSICAL_SCHEMA = "org.spicetrellis.physical-library"
PHYSICAL_VERSION = 1
MAX_PHYSICAL_BYTES = 8 * 1024 * 1024
_INTEGER = re.compile(r"(?:0|-?[1-9][0-9]{0,19})\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class _Array:
    """A borrowed immutable array, not an eagerly converted JSON subtree."""

    values: tuple[object, ...]


def _wire_value(value: object) -> object:
    """Convert one node only; children remain immutable model references."""
    if type(value) is Point:
        return _Array((str(value.x), str(value.y)))
    if type(value) is Rectangle:
        return {
            "kind": "rectangle",
            "origin": value.origin,
            "size": _Array((str(value.width), str(value.height))),
        }
    if type(value) is Polygon:
        return {"kind": "polygon", "vertices": _Array(value.vertices)}
    if type(value) is Path:
        return {"kind": "path", "points": _Array(value.points), "width": str(value.width)}
    if type(value) is PhysicalShape:
        return {"layer": value.layer, "net": value.net, "geometry": value.geometry}
    if type(value) is Annotation:
        return {"text": value.text, "at": value.at}
    if type(value) is Transform:
        return {"origin": value.origin, "clockwise": value.clockwise, "reflect_x": value.reflect_x}
    if type(value) is PhysicalInstance:
        return {"name": value.name, "cell": value.cell, "transform": value.transform}
    if type(value) is PhysicalPort:
        return {"name": value.name, "shapes": _Array(value.shapes)}
    if type(value) is AbstractView:
        return {
            "outline": value.outline,
            "ports": _Array(value.ports),
            "blockages": _Array(value.blockages),
        }
    if type(value) is LayoutView:
        return {
            "shapes": _Array(value.shapes),
            "instances": _Array(value.instances),
            "annotations": _Array(value.annotations),
        }
    if type(value) is PhysicalCell:
        return {
            "name": value.name,
            "ports": _Array(value.ports),
            "circuit_module": value.circuit_module,
            "layout": value.layout,
            "abstract": value.abstract,
        }
    if type(value) is LayerSpec:
        return {
            "id": value.id,
            "number": str(value.number),
            "datatype": str(value.datatype),
            "purpose": value.purpose.value,
            "description": value.description,
        }
    if type(value) is Technology:
        return {
            "name": value.name,
            "layers": _Array(value.layers),
            "packages": _Array(value.packages),
        }
    if type(value) is PhysicalLibrary:
        return {
            "schema": PHYSICAL_SCHEMA,
            "version": PHYSICAL_VERSION,
            "name": value.name,
            "unit": value.unit.value,
            "technology": value.technology,
            "cells": _Array(value.cells),
        }
    return value


def _detached(value: object) -> object:
    value = _wire_value(value)
    if isinstance(value, dict):
        return {key: _detached(child) for key, child in value.items()}
    if isinstance(value, _Array):
        return [_detached(child) for child in value.values]
    return value


def physical_data(library: PhysicalLibrary) -> dict[str, object]:
    """Explicitly materialize a detached tree; use dump_physical for bounded encoding."""
    if type(library) is not PhysicalLibrary:
        raise PhysicalError("physical_data requires a PhysicalLibrary")
    result = _detached(library)
    if not isinstance(result, dict):
        raise PhysicalError("physical library did not produce an object")
    return result


def _chunks(value: object) -> Iterator[str]:
    value = _wire_value(value)
    if isinstance(value, dict):
        yield "{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=False)
            yield ":"
            yield from _chunks(value[key])
        yield "}"
    elif isinstance(value, _Array):
        yield "["
        for index, child in enumerate(value.values):
            if index:
                yield ","
            yield from _chunks(child)
        yield "]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def dump_physical(library: PhysicalLibrary) -> str:
    """Canonical UTF-8 JSON text, terminated by one newline, with a wire-size cap."""
    if type(library) is not PhysicalLibrary:
        raise PhysicalError("dump_physical requires a PhysicalLibrary")
    parts: list[str] = []
    size = 1
    for chunk in _chunks(library):
        size += len(chunk.encode("utf-8"))
        if size > MAX_PHYSICAL_BYTES:
            raise PhysicalError("physical JSON exceeds the byte budget")
        parts.append(chunk)
    return "".join(parts) + "\n"


def physical_digest(library: PhysicalLibrary) -> str:
    return hashlib.sha256(dump_physical(library).encode("utf-8")).hexdigest()


def _object(value: object, keys: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise PhysicalError(f"physical object requires exactly these fields: {keys}")
    return value


def _array(value: object, maximum: int = MAX_ITEMS) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise PhysicalError(f"physical array must contain at most {maximum} items")
    return value


def _text(value: object) -> str:
    if type(value) is not str or len(value) > MAX_TEXT:
        raise PhysicalError("physical text field must be a bounded string")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _integer(value: object, *, unsigned: bool = False) -> int:
    if type(value) is not str or _INTEGER.fullmatch(value) is None:
        raise PhysicalError("64-bit fields require canonical decimal strings")
    result = int(value)
    low, high = (0, 2**64 - 1) if unsigned else (-(2**63), 2**63 - 1)
    if not low <= result <= high:
        raise PhysicalError("decimal string is outside the 64-bit field range")
    return result


def _point(value: object) -> Point:
    values = _array(value, 2)
    if len(values) != 2:
        raise PhysicalError("a point requires exactly two coordinates")
    return Point(_integer(values[0]), _integer(values[1]))


@dataclass(slots=True)
class _Budget:
    items: int = 0
    points: int = 0
    polygon_work: int = 0

    def add(self, *, items: int = 0, points: int = 0, work: int = 0) -> None:
        self.items += items
        self.points += points
        self.polygon_work += work
        if (
            self.items > MAX_ITEMS
            or self.points > MAX_STORED_POINTS
            or self.polygon_work > MAX_STORED_POLYGON_WORK
        ):
            raise PhysicalError("physical JSON exceeds aggregate item/point/polygon-work budget")


def _geometry(value: object, budget: _Budget) -> Rectangle | Polygon | Path:
    if not isinstance(value, dict):
        raise PhysicalError("physical geometry must be an object")
    kind = value.get("kind")
    if kind == "rectangle":
        data = _object(value, "kind origin size")
        sizes = _array(data["size"], 2)
        if len(sizes) != 2:
            raise PhysicalError("rectangle size requires two dimensions")
        budget.add(points=4, work=16)
        return Rectangle(_point(data["origin"]), _integer(sizes[0]), _integer(sizes[1]))
    if kind == "polygon":
        data = _object(value, "kind vertices")
        vertices = _array(data["vertices"], 1_024)
        budget.add(points=len(vertices), work=len(vertices) ** 2)
        return Polygon(tuple(_point(point) for point in vertices))
    if kind == "path":
        data = _object(value, "kind points width")
        points = _array(data["points"])
        budget.add(points=len(points))
        return Path(tuple(_point(point) for point in points), _integer(data["width"]))
    raise PhysicalError("unknown physical geometry kind")


def _shape(value: object, budget: _Budget) -> PhysicalShape:
    data = _object(value, "layer geometry net")
    budget.add(items=1)
    return PhysicalShape(
        _text(data["layer"]), _geometry(data["geometry"], budget), _optional_text(data["net"])
    )


def _abstract(value: object, budget: _Budget) -> AbstractView:
    data = _object(value, "outline ports blockages")
    outline = _geometry(data["outline"], budget)
    if type(outline) is not Polygon:
        raise PhysicalError("abstract outline must be a polygon")
    ports: list[PhysicalPort] = []
    for value in _array(data["ports"]):
        port = _object(value, "name shapes")
        budget.add(items=1)
        ports.append(
            PhysicalPort(
                _text(port["name"]),
                tuple(_shape(shape, budget) for shape in _array(port["shapes"])),
            )
        )
    return AbstractView(
        outline, tuple(ports), tuple(_shape(shape, budget) for shape in _array(data["blockages"]))
    )


def _layout(value: object, budget: _Budget) -> LayoutView:
    data = _object(value, "shapes instances annotations")
    instances: list[PhysicalInstance] = []
    annotations: list[Annotation] = []
    for value in _array(data["instances"]):
        instance = _object(value, "name cell transform")
        transform = _object(instance["transform"], "origin clockwise reflect_x")
        budget.add(items=1)
        angle, reflect = transform["clockwise"], transform["reflect_x"]
        if type(angle) is not int or type(reflect) is not bool:
            raise PhysicalError("transform angle/reflection require integer/boolean values")
        instances.append(
            PhysicalInstance(
                _text(instance["name"]),
                _text(instance["cell"]),
                Transform(_point(transform["origin"]), angle, reflect),
            )
        )
    for value in _array(data["annotations"]):
        annotation = _object(value, "text at")
        budget.add(items=1, points=1)
        annotations.append(Annotation(_text(annotation["text"]), _point(annotation["at"])))
    return LayoutView(
        tuple(_shape(shape, budget) for shape in _array(data["shapes"])),
        tuple(instances),
        tuple(annotations),
    )


def _library(value: object) -> PhysicalLibrary:
    data = _object(value, "schema version name unit technology cells")
    if (
        data["schema"] != PHYSICAL_SCHEMA
        or type(data["version"]) is not int
        or data["version"] != PHYSICAL_VERSION
    ):
        raise PhysicalError("unsupported physical schema or version")
    technology = _object(data["technology"], "name layers packages")
    layers: list[LayerSpec] = []
    for value in _array(technology["layers"], MAX_DEFINITIONS):
        layer = _object(value, "id number datatype purpose description")
        layers.append(
            LayerSpec(
                _text(layer["id"]),
                _integer(layer["number"], unsigned=True),
                _integer(layer["datatype"], unsigned=True),
                LayerPurpose(_text(layer["purpose"])),
                _text(layer["description"]),
            )
        )
    tech = Technology(
        _text(technology["name"]),
        tuple(layers),
        tuple(_text(name) for name in _array(technology["packages"], MAX_DEFINITIONS)),
    )
    budget = _Budget()
    cells: list[PhysicalCell] = []
    for value in _array(data["cells"], MAX_DEFINITIONS):
        cell = _object(value, "name ports layout abstract circuit_module")
        ports = _array(cell["ports"])
        budget.add(items=len(ports))
        cells.append(
            PhysicalCell(
                _text(cell["name"]),
                tuple(_text(port) for port in ports),
                None if cell["layout"] is None else _layout(cell["layout"], budget),
                None if cell["abstract"] is None else _abstract(cell["abstract"], budget),
                _optional_text(cell["circuit_module"]),
            )
        )
    return PhysicalLibrary(
        _text(data["name"]), DistanceUnit(_text(data["unit"])), tech, tuple(cells)
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalError("duplicate JSON object member")
        result[key] = value
    return result


def _reject_number(value: str) -> NoReturn:
    raise PhysicalError("floating-point and non-finite JSON numbers are not part of physical v1")


def load_physical_text(text: str) -> PhysicalLibrary:
    """Decode strict JSON without duplicate keys, float coercion or geometry loss."""
    if type(text) is not str or len(text) > MAX_PHYSICAL_BYTES:
        raise PhysicalError("physical JSON must be bounded text")
    try:
        if len(text.encode("utf-8")) > MAX_PHYSICAL_BYTES:
            raise PhysicalError("physical JSON exceeds the byte budget")
        value: object = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        return _library(value)
    except PhysicalError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError, GeometryError) as error:
        raise PhysicalError(f"invalid physical JSON: {error}") from error


def load_physical(path: str | FilePath) -> PhysicalLibrary:
    """Read at most the wire limit plus one byte; never read an unbounded file."""
    try:
        with FilePath(path).open("rb") as stream:
            content = stream.read(MAX_PHYSICAL_BYTES + 1)
        if len(content) > MAX_PHYSICAL_BYTES:
            raise PhysicalError("physical JSON file exceeds the byte budget")
        return load_physical_text(content.decode("utf-8"))
    except PhysicalError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise PhysicalError(f"cannot read physical JSON: {error}") from error


def write_physical(
    library: PhysicalLibrary,
    path: str | FilePath,
    *,
    force: bool = False,
    protected: tuple[FilePath, ...] = (),
) -> None:
    """Atomically write canonical JSON, without clobbering by default or input aliases."""
    try:
        write_text_atomic(path, dump_physical(library), force=force, protected=protected)
    except (OSError, ValueError) as error:
        raise PhysicalError(f"cannot write physical JSON: {error}") from error
