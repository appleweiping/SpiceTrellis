# Physical library and exact geometry

The physical library is an independent immutable boundary alongside circuit IR.
It represents raw geometry, physical interfaces and a technology layer map. It
does not execute an extractor, place/route a design or certify fabrication rules.

## Construct, serialize and expand

```python
from spicetrellis.geometry import Point, Rectangle, Transform
from spicetrellis.physical import (
    DistanceUnit,
    LayerPurpose,
    LayerSpec,
    LayoutView,
    PhysicalCell,
    PhysicalInstance,
    PhysicalLibrary,
    PhysicalShape,
    Technology,
    flatten_layout,
)
from spicetrellis.physical_json import dump_physical, load_physical_text

technology = Technology("demo", (LayerSpec("metal1.drawing", 1, 0, LayerPurpose.DRAWING),))
leaf = PhysicalCell(
    "leaf",
    layout=LayoutView(
        shapes=(PhysicalShape("metal1.drawing", Rectangle(Point(0, 0), 20, 40), "local"),)
    ),
)
top = PhysicalCell(
    "top",
    layout=LayoutView(
        instances=(
            PhysicalInstance("left", "leaf"),
            PhysicalInstance("right", "leaf", Transform(Point(100, 0), 90, True)),
        )
    ),
)
library = PhysicalLibrary("demo", DistanceUnit.NANOMETER, technology, (top, leaf))
assert load_physical_text(dump_physical(library)) == library
flat = flatten_layout(library, "top")
assert flat.expanded_cells == 3
assert tuple(item.instance_path for item in flat.shapes) == (("left",), ("right",))
```

All public constructors reject mutable lists in tuple fields, invalid nested
types, ambiguous names and duplicate identities. Cells may carry raw layout,
physical abstract and a circuit-module reference independently. Abstract ports
must be members of the ordered cell interface; their explicit net labels must
match their port names. Referenced numeric layer-purpose pairs must be declared
in the technology, without duplicate IDs or numeric pairs. Layer purpose is a
semantic label, not a material-conductivity model.

The circuit-module field preserves a cross-view reference. At present it does
not prove that an external circuit document exists or that its connectivity
matches the geometry. Physical labels are local to their instance path; equal
labels, overlapping shapes and annotations do not implicitly short nets together.
Abstract outlines/blockages are retained, but no containment DRC is asserted.

## Integer geometry

Coordinates and dimensions use signed 64-bit integers in the library's declared
micrometer, nanometer or angstrom unit. Dimensions are positive, and calculated
vertices must remain in range. Area, orientation, winding-number containment and
segment intersection use exact Python integers/Fractions, never float tolerances.
Rectangles, simple polygons without holes and constant-width centerline paths
are supported. Polygon closure is implicit; duplicate vertices, zero-area,
backtracking and non-adjacent touching/crossing edges are rejected. Collinear
forward vertices are preserved. Paths may self-cross but not repeat consecutive
points. Path bounds are a conservative square-cap envelope, not polygonization
or an area/DRC claim. Their half-unit endpoints remain exact and in range.

`Transform` reflects about the local x-axis, rotates clockwise by 0/90/180/270
degrees, then translates. `outer.compose(inner)` means `outer(inner(point))`.
All eight Manhattan isometries have exact inverses when the resulting translation
is representable. Intermediate unbounded arithmetic is allowed; final coordinate
overflow is a typed error. Other angles are explicitly unsupported, not rounded.
Flattened rectangles become exact four-vertex polygons; paths keep their width.

## Hierarchy and resource contract

Definitions can be in any order. An iterative dependency walk rejects undefined
cells, cycles and paths deeper than 64 cells, including diamonds whose shared
children were visited earlier. Flattening only uses raw layout views; a reachable
abstract-only or circuit-only cell raises an error instead of becoming empty.

Before transforming a point, dynamic programming computes the complete reachable
expansion cost, saturating at each limit plus one. There is no partial/truncated
success. The default limits are 100,000 expanded cells, 100,000 shapes, 1,000,000
shape points, 10,000,000 polygon-work units and 100,000 annotations. Polygon work
is the sum of squared polygon vertex counts (16 per rectangle), a conservative
accounting bound for the exact quadratic intersection validator, not wall time.
`FlattenLimits` can lower limits or raise them to explicit hard ceilings of
1,000,000 cells/shapes/annotations, 4,000,000 points and 100,000,000 work units.
Choose limits appropriate to available memory; a high ceiling is not a guarantee
that every machine can materialize that workload.

Stored libraries separately cap 4,096 cells/layers/packages, 100,000 aggregate
shape/port/instance/annotation entries, 1,000,000 points, 10,000,000 polygon-work
units, 4,096 characters per text field and 8 MiB aggregate UTF-8 text. These are
definition costs, not expanded-instance estimates.

## Wire format v1

A runnable, original example includes rectangles, a path, polygon outlines,
abstract port access shapes and three independently scoped placements:

```console
python examples/physical_library.py library.json
spice-trellis check-physical library.json --top top
```

It reports four expanded cells, ten raw shapes, four annotations and bounds
`[-30, -30, 190, 30]` in nanometers. Abstract access shapes are preserved in the
library, not duplicated into its raw flattened layout. This is illustrative
geometry, not a resistor model, a PDK cell, extracted connectivity or a DRC result.
Its `symbolic.two_terminal` circuit reference is deliberately not resolved.

```console
spice-trellis check-physical library.json
spice-trellis check-physical library.json --top top --max-shapes 10000
spice-trellis normalize-physical library.json --output canonical.json
```

The first command validates definitions. `--top` also materializes the requested
layout and reports expanded counts and exact bounds. Normalization refuses to
overwrite a destination without `--force`, and even force cannot overwrite the
source or its filesystem aliases. The Python `write_physical` API uses the same
atomic no-clobber writer and accepts an explicit tuple of protected input paths.

The schema is `org.spicetrellis.physical-library`, version 1. See
[the JSON Schema](schemas/physical-library-v1.schema.json). Every 64-bit field
(coordinates, sizes, numeric layer and purpose codes) is a **canonical decimal
string**, so a consumer does not lose low bits through JSON binary64 numbers.
No leading plus, leading zero, negative zero, exponent or Unicode digit is
accepted. Version and the small rotation enum remain JSON integers.

The semantic loader enforces exact integer ranges, referential and geometric
validity and aggregate budgets beyond the schema's wire shape. Duplicate object
members, unknown fields, float/non-finite literals, invalid UTF-8 and more than
8 MiB of encoded JSON are errors. The decoder charges polygon work before
constructing each polygon. `dump_physical` sorts object keys, preserves declared
array order and Unicode spelling, and appends one newline. `physical_digest`
hashes these exact UTF-8 bytes; it is a content identity, not geometric equivalence.

Encoding traverses immutable model nodes lazily and enforces its byte budget
without first creating a second geometry tree. The separate `physical_data` API
explicitly requests a detached mutable tree; it is not the streaming writer.

Raw JSON v1 does not claim GDS/OASIS import/export, gridded track placement,
arbitrary-angle transforms, shape Booleans, extraction or foundry sign-off.
Those are separate capabilities, not silently represented by empty adapters.
