"""Primitive contracts and adversarial cases for integer physical geometry."""

from dataclasses import FrozenInstanceError
from fractions import Fraction
from itertools import product

import pytest

from spicetrellis.geometry import (
    INT64_MAX,
    INT64_MIN,
    MAX_PATH_POINTS,
    MAX_POLYGON_VERTICES,
    Bounds,
    GeometryError,
    Path,
    Point,
    Polygon,
    Rectangle,
    Transform,
)


@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, INT64_MIN - 1, INT64_MAX + 1])
def test_coordinates_are_exact_signed64(value):
    with pytest.raises(GeometryError, match="signed 64-bit"):
        Point(value, 0)
    with pytest.raises(GeometryError, match="signed 64-bit"):
        Point(0, value)


def test_immutable_point_extremes():
    point = Point(INT64_MIN, INT64_MAX)
    with pytest.raises(FrozenInstanceError):
        point.x = 1
    assert hash(point) == hash(Point(INT64_MIN, INT64_MAX))


def test_bounds_exact_union_and_boundary():
    bounds = Bounds(Fraction(-1, 2), -1, 4, Fraction(7, 2))
    assert bounds.contains(Point(0, -1))
    assert not bounds.contains(Point(-1, 0))
    assert not bounds.contains(Point(5, 0))
    assert not bounds.contains(Point(0, 4))
    assert bounds.union(Bounds(-2, 0, 1, 5)) == Bounds(-2, -1, 4, 5)
    with pytest.raises(GeometryError):
        bounds.union(None)
    with pytest.raises(GeometryError):
        bounds.contains((0, 0))


@pytest.mark.parametrize("values", [(0.0, 0, 1, 1), (False, 0, 1, 1), (2, 0, 1, 1), (0, 2, 1, 1)])
def test_invalid_bounds(values):
    with pytest.raises(GeometryError):
        Bounds(*values)


def test_rectangle_area_does_not_overflow_or_round():
    shape = Rectangle(Point(-10, -20), 11, 23)
    assert shape.area == 253
    assert shape.bounds == Bounds(-10, -20, 1, 3)
    assert shape.polygon().area == 253
    huge = Rectangle(Point(INT64_MIN, INT64_MIN), INT64_MAX, INT64_MAX)
    assert huge.area == INT64_MAX**2


@pytest.mark.parametrize(
    "args",
    [
        ((0, 0), 1, 1),
        (Point(0, 0), 0, 1),
        (Point(0, 0), 1, -1),
        (Point(0, 0), True, 1),
        (Point(INT64_MAX, 0), 1, 1),
        (Point(0, INT64_MAX), 1, 1),
    ],
)
def test_invalid_rectangle(args):
    with pytest.raises(GeometryError):
        Rectangle(*args)


def test_concave_polygon_area_boundary_and_orientation():
    points = tuple(Point(*p) for p in ((0, 0), (5, 0), (5, 2), (2, 2), (2, 5), (0, 5)))
    for vertices in (points, tuple(reversed(points))):
        shape = Polygon(vertices)
        assert shape.area == 16
        assert shape.bounds == Bounds(0, 0, 5, 5)
        assert shape.contains(Point(1, 1))
        assert not shape.contains(Point(3, 3))
        assert not shape.contains(Point(6, 1))
        assert shape.contains(Point(2, 3))
        assert not shape.contains(Point(2, 3), boundary=False)
        assert shape.contains(Point(0, 0))
        with pytest.raises(GeometryError):
            shape.contains(None)
        with pytest.raises(GeometryError):
            shape.contains(Point(1, 1), boundary=1)
    assert Polygon(points).signed_double_area == 32
    assert Polygon(tuple(reversed(points))).signed_double_area == -32


def test_half_unit_area_and_collinear_forward_vertex():
    assert Polygon((Point(0, 0), Point(1, 0), Point(0, 1))).area == Fraction(1, 2)
    polygon = Polygon((Point(0, 0), Point(1, 0), Point(2, 0), Point(2, 2), Point(0, 2)))
    assert polygon.area == 4


@pytest.mark.parametrize(
    "vertices",
    [
        [],
        (),
        (Point(0, 0), Point(1, 1)),
        ((0, 0), (1, 0), (0, 1)),
        (Point(0, 0),) * (MAX_POLYGON_VERTICES + 1),
        (Point(0, 0), Point(1, 0), Point(0, 0)),
        (Point(0, 0), Point(2, 2), Point(0, 2), Point(2, 0)),
        (Point(0, 0), Point(3, 0), Point(1, 0), Point(0, 1)),
        (Point(0, 0), Point(1, 0), Point(2, 0)),
        (Point(0, 0), Point(4, 0), Point(4, 4), Point(2, 0), Point(0, 4)),
    ],
)
def test_invalid_polygons(vertices):
    with pytest.raises(GeometryError):
        Polygon(vertices)


def test_path_bounds_are_exact_conservative_envelope():
    path = Path((Point(0, 0), Point(4, 6), Point(-2, 8)), 3)
    assert path.bounds == Bounds(Fraction(-7, 2), Fraction(-3, 2), Fraction(11, 2), Fraction(19, 2))
    assert Path((Point(0, 0), Point(2, 0), Point(0, 0)), 2).bounds == Bounds(-1, -1, 3, 1)


@pytest.mark.parametrize(
    "points,width",
    [
        ([], 1),
        ((Point(0, 0),), 1),
        ((Point(0, 0), Point(0, 0)), 1),
        ((Point(0, 0), Point(1, 1)), 0),
        ((Point(0, 0),) * (MAX_PATH_POINTS + 1), 1),
        ((Point(INT64_MIN, 0), Point(0, 0)), 1),
        ((Point(0, INT64_MIN), Point(0, 0)), 1),
        ((Point(INT64_MAX, 0), Point(0, 0)), 1),
        ((Point(0, INT64_MAX), Point(0, 0)), 1),
    ],
)
def test_invalid_paths(points, width):
    with pytest.raises(GeometryError):
        Path(points, width)


@pytest.mark.parametrize("angle,reflect", tuple(product((0, 90, 180, 270), (False, True))))
def test_isometries_preserve_area_and_round_trip(angle, reflect):
    transform = Transform(Point(13, -7), angle, reflect)
    shape = Rectangle(Point(0, 0), 2, 7)
    result = transform.shape(shape)
    assert result.area == shape.area
    expected_sign = -1 if reflect else 1
    assert result.signed_double_area == expected_sign * 28
    for point in shape.polygon().vertices:
        assert transform.inverse().apply(transform.apply(point)) == point
    path = Path((Point(0, 0), Point(5, 5)), 3)
    assert transform.inverse().shape(transform.shape(path)) == path


def test_transform_translation_occurs_after_reflection_then_rotation():
    assert Transform(Point(10, 20), 90, True).apply(Point(3, 5)) == Point(5, 17)
    # The intermediate reflected coordinate exceeds int64 but the final does not.
    transform = Transform(Point(-1, 0), 180)
    assert transform.apply(Point(INT64_MIN, 0)) == Point(INT64_MAX, 0)
    assert transform.inverse().apply(Point(INT64_MAX, 0)) == Point(INT64_MIN, 0)
    assert Transform(Point(INT64_MIN, 0), 180).inverse().origin == Point(INT64_MIN, 0)
    with pytest.raises(GeometryError):
        Transform(Point(INT64_MIN, 0)).inverse()
    with pytest.raises(GeometryError):
        Transform(clockwise=180).apply(Point(INT64_MIN, 0))


@pytest.mark.parametrize("angle", [-90, 360, 45, True, 90.0, "90"])
def test_unsupported_rotations_are_never_rounded(angle):
    with pytest.raises(GeometryError):
        Transform(clockwise=angle)


def test_invalid_transform_inputs():
    with pytest.raises(GeometryError):
        Transform(origin=(0, 0))
    with pytest.raises(GeometryError):
        Transform(reflect_x=1)
    with pytest.raises(GeometryError):
        Transform().apply((0, 0))
    with pytest.raises(GeometryError):
        Transform().compose(None)
    with pytest.raises(GeometryError):
        Transform().shape(None)


def test_all_orientations_compose_in_function_order():
    transforms = [
        Transform(Point(7, -3), a, r) for a, r in product((0, 90, 180, 270), (False, True))
    ]
    for outer, inner in product(transforms, repeat=2):
        for point in (Point(0, 0), Point(1, 0), Point(0, 1), Point(-9, 6)):
            assert outer.compose(inner).apply(point) == outer.apply(inner.apply(point))
