"""Exact database-unit geometry for physical views.

Coordinates are signed 64-bit integers. Bounds may be half-integral for odd-width
paths. No floating-point tolerance changes connectivity, area or orientation.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
MAX_POLYGON_VERTICES = 1_024
MAX_PATH_POINTS = 100_000


class GeometryError(ValueError):
    """Invalid, unsupported or out-of-range database-unit geometry."""


def coordinate(value: int, name: str = "coordinate") -> int:
    """Require an actual integer in the wire contract, never bool or float."""
    if type(value) is not int or not INT64_MIN <= value <= INT64_MAX:
        raise GeometryError(f"{name} must be a signed 64-bit integer")
    return value


def _positive(value: int, name: str) -> None:
    coordinate(value, name)
    if value <= 0:
        raise GeometryError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def __post_init__(self) -> None:
        coordinate(self.x, "x")
        coordinate(self.y, "y")


@dataclass(frozen=True, slots=True)
class Bounds:
    """Closed signed64-range bounds on the half-unit grid, including path envelopes."""

    left: Fraction | int
    bottom: Fraction | int
    right: Fraction | int
    top: Fraction | int

    def __post_init__(self) -> None:
        for value in (self.left, self.bottom, self.right, self.top):
            if type(value) not in (int, Fraction):
                raise GeometryError("bounds require exact integers or fractions")
            if value < INT64_MIN or value > INT64_MAX:
                raise GeometryError("bounds exceed signed 64-bit coordinate range")
            if isinstance(value, Fraction) and value.denominator not in (1, 2):
                raise GeometryError("bounds must lie on the integer or half-unit grid")
        if self.left > self.right or self.bottom > self.top:
            raise GeometryError("bounds must be ordered")

    def union(self, other: Bounds) -> Bounds:
        if type(other) is not Bounds:
            raise GeometryError("union requires Bounds")
        return Bounds(
            min(self.left, other.left),
            min(self.bottom, other.bottom),
            max(self.right, other.right),
            max(self.top, other.top),
        )

    def contains(self, point: Point) -> bool:
        if type(point) is not Point:
            raise GeometryError("containment requires a Point")
        return self.left <= point.x <= self.right and self.bottom <= point.y <= self.top


def _point_tuple(points: tuple[Point, ...], minimum: int, maximum: int) -> None:
    if type(points) is not tuple or not minimum <= len(points) <= maximum:
        raise GeometryError(f"points must be an immutable tuple of {minimum}..{maximum} points")
    if any(type(point) is not Point for point in points):
        raise GeometryError("each vertex must be a Point")


def _bounds(points: tuple[Point, ...]) -> Bounds:
    return Bounds(
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _cross(a: Point, b: Point, c: Point) -> int:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _between(a: Point, b: Point, point: Point) -> bool:
    return min(a.x, b.x) <= point.x <= max(a.x, b.x) and min(a.y, b.y) <= point.y <= max(a.y, b.y)


def _intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if (ab_c > 0 > ab_d or ab_d > 0 > ab_c) and (cd_a > 0 > cd_b or cd_b > 0 > cd_a):
        return True
    return (
        (ab_c == 0 and _between(a, b, c))
        or (ab_d == 0 and _between(a, b, d))
        or (cd_a == 0 and _between(c, d, a))
        or (cd_b == 0 and _between(c, d, b))
    )


def _orient(point: Point, clockwise: int, reflect_x: bool) -> tuple[int, int]:
    x, y = point.x, -point.y if reflect_x else point.y
    if clockwise == 90:
        return y, -x
    if clockwise == 180:
        return -x, -y
    if clockwise == 270:
        return -y, x
    return x, y


@dataclass(frozen=True, slots=True)
class Polygon:
    """A simple polygon without holes; the closing vertex is implicit.

    Validation uses at most 1,024 vertices and quadratic exact segment tests.
    Clockwise and counter-clockwise vertex order are both retained losslessly.
    """

    vertices: tuple[Point, ...]

    def __post_init__(self) -> None:
        _point_tuple(self.vertices, 3, MAX_POLYGON_VERTICES)
        if len(set(self.vertices)) != len(self.vertices):
            raise GeometryError("polygon vertices must be distinct; closure is implicit")
        count = len(self.vertices)
        for index, a in enumerate(self.vertices):
            b, c = self.vertices[(index + 1) % count], self.vertices[(index + 2) % count]
            if _cross(a, b, c) == 0 and not _between(a, c, b):
                raise GeometryError("adjacent polygon edges may not backtrack")
            for other in range(index + 2, count):
                if index == 0 and other == count - 1:
                    continue  # Adjacent closing edge shares the first vertex.
                if _intersects(a, b, self.vertices[other], self.vertices[(other + 1) % count]):
                    raise GeometryError("polygon edges may not intersect or touch non-adjacently")
        if self.signed_double_area == 0:
            raise GeometryError("polygon must have non-zero area")

    @property
    def signed_double_area(self) -> int:
        return sum(
            a.x * b.y - b.x * a.y
            for a, b in zip(self.vertices, (*self.vertices[1:], self.vertices[0]), strict=True)
        )

    @property
    def area(self) -> Fraction:
        return Fraction(abs(self.signed_double_area), 2)

    @property
    def bounds(self) -> Bounds:
        return _bounds(self.vertices)

    def contains(self, point: Point, *, boundary: bool = True) -> bool:
        """Exact winding-number containment, with an explicit boundary policy."""
        if type(point) is not Point or type(boundary) is not bool:
            raise GeometryError("containment needs a Point and boolean boundary policy")
        winding = 0
        for a, b in zip(self.vertices, (*self.vertices[1:], self.vertices[0]), strict=True):
            cross = _cross(a, b, point)
            if cross == 0 and _between(a, b, point):
                return boundary
            if a.y <= point.y < b.y and cross > 0:
                winding += 1
            elif b.y <= point.y < a.y and cross < 0:
                winding -= 1
        return winding != 0


@dataclass(frozen=True, slots=True)
class Rectangle:
    origin: Point
    width: int
    height: int

    def __post_init__(self) -> None:
        if type(self.origin) is not Point:
            raise GeometryError("rectangle origin must be a Point")
        _positive(self.width, "width")
        _positive(self.height, "height")
        coordinate(self.origin.x + self.width, "rectangle right edge")
        coordinate(self.origin.y + self.height, "rectangle top edge")

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def bounds(self) -> Bounds:
        return Bounds(
            self.origin.x, self.origin.y, self.origin.x + self.width, self.origin.y + self.height
        )

    def polygon(self) -> Polygon:
        x, y = self.origin.x, self.origin.y
        return Polygon(
            (
                Point(x, y),
                Point(x + self.width, y),
                Point(x + self.width, y + self.height),
                Point(x, y + self.height),
            )
        )


@dataclass(frozen=True, slots=True)
class Path:
    """Constant-width centerline; its bounds conservatively include square caps.

    Cap/join polygonization is not implicit. These bounds are not a fabricated
    area, DRC clearance, or Boolean union of segments.
    """

    points: tuple[Point, ...]
    width: int

    def __post_init__(self) -> None:
        _point_tuple(self.points, 2, MAX_PATH_POINTS)
        _positive(self.width, "path width")
        if any(a == b for a, b in zip(self.points, self.points[1:], strict=False)):
            raise GeometryError("consecutive path points must differ")
        # Bounds validates half-grid endpoints, including the stroke envelope.
        _ = self.bounds

    @property
    def bounds(self) -> Bounds:
        center = _bounds(self.points)
        half = Fraction(self.width, 2)
        return Bounds(
            center.left - half, center.bottom - half, center.right + half, center.top + half
        )


@dataclass(frozen=True, slots=True)
class Transform:
    """Reflect in the local x-axis, rotate clockwise, then translate.

    The eight Manhattan orientations are exact integer isometries. Arbitrary
    rotations are rejected, rather than rounded onto a different physical grid.
    Composition follows function application: ``a.compose(b)(p) == a(b(p))``.
    """

    origin: Point = Point(0, 0)
    clockwise: int = 0
    reflect_x: bool = False

    def __post_init__(self) -> None:
        if type(self.origin) is not Point or type(self.reflect_x) is not bool:
            raise GeometryError("transform requires a Point origin and boolean reflection")
        if type(self.clockwise) is not int or self.clockwise not in (0, 90, 180, 270):
            raise GeometryError("only exact 0/90/180/270 degree clockwise rotations are supported")

    def apply(self, point: Point) -> Point:
        if type(point) is not Point:
            raise GeometryError("transform input must be a Point")
        # Do not range-check the intermediate point: translation may bring a
        # reflected INT64_MIN coordinate back into the representable domain.
        x, y = _orient(point, self.clockwise, self.reflect_x)
        return Point(x + self.origin.x, y + self.origin.y)

    def compose(self, inner: Transform) -> Transform:
        if type(inner) is not Transform:
            raise GeometryError("composition requires a Transform")
        rotation = (
            self.clockwise + (-inner.clockwise if self.reflect_x else inner.clockwise)
        ) % 360
        return Transform(self.apply(inner.origin), rotation, self.reflect_x != inner.reflect_x)

    def inverse(self) -> Transform:
        rotation = self.clockwise if self.reflect_x else (-self.clockwise) % 360
        # Negation and the inverse linear map must be evaluated before checking
        # the final range; e.g. a reflection can make -INT64_MIN representable.
        x, y = _orient(self.origin, rotation, self.reflect_x)
        return Transform(Point(-x, -y), rotation, self.reflect_x)

    def shape(self, shape: Rectangle | Polygon | Path) -> Polygon | Path:
        if type(shape) is Rectangle:
            x, y = shape.origin.x, shape.origin.y
            return Polygon(
                tuple(
                    self.apply(point)
                    for point in (
                        Point(x, y),
                        Point(x + shape.width, y),
                        Point(x + shape.width, y + shape.height),
                        Point(x, y + shape.height),
                    )
                )
            )
        if type(shape) is Polygon:
            return Polygon(tuple(self.apply(point) for point in shape.vertices))
        if type(shape) is Path:
            return Path(tuple(self.apply(point) for point in shape.points), shape.width)
        raise GeometryError("unsupported shape type")
