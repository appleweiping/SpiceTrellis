"""Create an original illustrative raw-layout library; not a foundry cell."""

from __future__ import annotations

import argparse
from pathlib import Path as FilePath

from spicetrellis.geometry import Path, Point, Polygon, Rectangle, Transform
from spicetrellis.physical import (
    AbstractView,
    Annotation,
    DistanceUnit,
    LayerPurpose,
    LayerSpec,
    LayoutView,
    PhysicalCell,
    PhysicalInstance,
    PhysicalLibrary,
    PhysicalPort,
    PhysicalShape,
    Technology,
)
from spicetrellis.physical_json import write_physical


def make_library() -> PhysicalLibrary:
    """Expose all three shape types, abstract ports and scoped repeated instances."""
    technology = Technology(
        "illustrative-not-a-pdk",
        (
            LayerSpec("metal.drawing", 1, 0, LayerPurpose.DRAWING),
            LayerSpec("metal.pin", 1, 1, LayerPurpose.PIN),
            LayerSpec("outline", 2, 0, LayerPurpose.OUTLINE),
        ),
    )
    left = Rectangle(Point(-20, -5), 10, 10)
    right = Rectangle(Point(10, -5), 10, 10)
    leaf = PhysicalCell(
        "two_terminal",
        ports=("a", "b"),
        layout=LayoutView(
            shapes=(
                PhysicalShape("metal.drawing", left, "a"),
                PhysicalShape("metal.drawing", right, "b"),
                PhysicalShape("metal.drawing", Path((Point(-10, 0), Point(10, 0)), 2), "body"),
            ),
            annotations=(Annotation("illustrative geometry", Point(0, 8)),),
        ),
        abstract=AbstractView(
            outline=Polygon((Point(-25, -10), Point(25, -10), Point(25, 10), Point(-25, 10))),
            ports=(
                PhysicalPort("a", (PhysicalShape("metal.pin", left, "a"),)),
                PhysicalPort("b", (PhysicalShape("metal.pin", right, "b"),)),
            ),
        ),
        circuit_module="symbolic.two_terminal",
    )
    top = PhysicalCell(
        "top",
        layout=LayoutView(
            shapes=(
                PhysicalShape(
                    "outline",
                    Polygon((Point(-30, -30), Point(190, -30), Point(190, 30), Point(-30, 30))),
                ),
            ),
            instances=(
                PhysicalInstance("plain", "two_terminal"),
                PhysicalInstance("rotated", "two_terminal", Transform(Point(80, 0), 90)),
                PhysicalInstance("reflected", "two_terminal", Transform(Point(160, 0), 0, True)),
            ),
            annotations=(Annotation("Three independent instance scopes", Point(0, 20)),),
        ),
    )
    return PhysicalLibrary("raw-layout-demo", DistanceUnit.NANOMETER, technology, (top, leaf))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=FilePath, help="new JSON file; existing files are protected")
    arguments = parser.parse_args()
    write_physical(make_library(), arguments.output)
