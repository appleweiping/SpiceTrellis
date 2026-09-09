"""Independent exact-arithmetic oracles for the physical geometry kernel."""

from __future__ import annotations

import math
import random
from fractions import Fraction
from itertools import product

import pytest

from spicetrellis.geometry import (
    INT64_MAX,
    INT64_MIN,
    Bounds,
    GeometryError,
    Point,
    Polygon,
    Transform,
)

Orientation = tuple[int, bool]
Matrix = tuple[int, int, int, int]
Pair = tuple[int, int]
BoundValues = tuple[Fraction | int, Fraction | int, Fraction | int, Fraction | int]

ORIENTATIONS: tuple[Orientation, ...] = tuple(product((0, 90, 180, 270), (False, True)))

# Explicit matrices for "reflect y, then rotate clockwise".  These values are
# deliberately not derived through Transform or its composition formula.
MATRICES: dict[Orientation, Matrix] = {
    (0, False): (1, 0, 0, 1),
    (90, False): (0, 1, -1, 0),
    (180, False): (-1, 0, 0, -1),
    (270, False): (0, -1, 1, 0),
    (0, True): (1, 0, 0, -1),
    (90, True): (0, -1, -1, 0),
    (180, True): (-1, 0, 0, 1),
    (270, True): (0, 1, 1, 0),
}


def _matrix_vector(matrix: Matrix, point: Pair) -> Pair:
    a, b, c, d = matrix
    x, y = point
    return a * x + b * y, c * x + d * y


def _matrix_product(outer: Matrix, inner: Matrix) -> Matrix:
    a, b, c, d = outer
    e, f, g, h = inner
    return a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h


def _oracle_apply(matrix: Matrix, origin: Pair, point: Pair) -> Pair:
    x, y = _matrix_vector(matrix, point)
    return x + origin[0], y + origin[1]


def _wire(transform: Transform) -> tuple[Matrix, Pair]:
    return MATRICES[(transform.clockwise, transform.reflect_x)], (
        transform.origin.x,
        transform.origin.y,
    )


def _representable(point: Pair) -> bool:
    return all(INT64_MIN <= value <= INT64_MAX for value in point)


def test_all_orientation_compositions_match_explicit_matrix_oracle() -> None:
    probes = ((0, 0), (1, 0), (0, 1), (-9, 6), (17, -23))
    for outer_orientation, inner_orientation in product(ORIENTATIONS, repeat=2):
        outer = Transform(Point(7, -3), *outer_orientation)
        inner = Transform(Point(-11, 5), *inner_orientation)
        composed = outer.compose(inner)

        expected_matrix = _matrix_product(MATRICES[outer_orientation], MATRICES[inner_orientation])
        expected_origin = _oracle_apply(MATRICES[outer_orientation], (7, -3), (-11, 5))
        assert _wire(composed) == (expected_matrix, expected_origin)
        for probe in probes:
            expected = _oracle_apply(expected_matrix, expected_origin, probe)
            assert composed.apply(Point(*probe)) == Point(*expected)


def test_all_orientation_inverses_match_matrix_transpose_oracle() -> None:
    for orientation in ORIENTATIONS:
        transform = Transform(Point(13, -29), *orientation)
        matrix = MATRICES[orientation]
        inverse_matrix = (matrix[0], matrix[2], matrix[1], matrix[3])
        transformed_origin = _matrix_vector(inverse_matrix, (13, -29))
        expected_origin = (-transformed_origin[0], -transformed_origin[1])

        inverse = transform.inverse()
        assert _wire(inverse) == (inverse_matrix, expected_origin)
        assert _matrix_product(inverse_matrix, matrix) == (1, 0, 0, 1)
        for probe in ((0, 0), (1, -2), (-31, 47)):
            assert inverse.apply(transform.apply(Point(*probe))) == Point(*probe)


def test_signed64_transform_domain_matches_unbounded_integer_oracle() -> None:
    values = (INT64_MIN, INT64_MIN + 1, -1, 0, 1, INT64_MAX - 1, INT64_MAX)
    origins: tuple[Pair, ...] = tuple((x, y) for x in values for y in values)
    probes = origins

    for orientation in ORIENTATIONS:
        matrix = MATRICES[orientation]
        for origin in origins:
            transform = Transform(Point(*origin), *orientation)
            for probe in probes:
                expected = _oracle_apply(matrix, origin, probe)
                if _representable(expected):
                    assert transform.apply(Point(*probe)) == Point(*expected)
                else:
                    with pytest.raises(GeometryError, match="signed 64-bit"):
                        transform.apply(Point(*probe))

            inverse_matrix = (matrix[0], matrix[2], matrix[1], matrix[3])
            mapped_origin = _matrix_vector(inverse_matrix, origin)
            expected_inverse_origin = (-mapped_origin[0], -mapped_origin[1])
            if _representable(expected_inverse_origin):
                assert _wire(transform.inverse()) == (inverse_matrix, expected_inverse_origin)
            else:
                with pytest.raises(GeometryError, match="signed 64-bit"):
                    transform.inverse()


def test_signed64_composition_translation_matches_integer_oracle() -> None:
    translations = (
        ((0, 0), (0, 0)),
        ((INT64_MIN, 0), (0, 0)),
        ((INT64_MAX, 0), (1, 0)),
        ((-1, 0), (INT64_MIN, 0)),
        ((0, INT64_MIN), (0, INT64_MAX)),
        ((INT64_MAX, INT64_MAX), (INT64_MAX, INT64_MAX)),
    )
    for outer_orientation, inner_orientation in product(ORIENTATIONS, repeat=2):
        outer_matrix = MATRICES[outer_orientation]
        expected_matrix = _matrix_product(outer_matrix, MATRICES[inner_orientation])
        for outer_origin, inner_origin in translations:
            outer = Transform(Point(*outer_origin), *outer_orientation)
            inner = Transform(Point(*inner_origin), *inner_orientation)
            expected_origin = _oracle_apply(outer_matrix, outer_origin, inner_origin)
            if _representable(expected_origin):
                assert _wire(outer.compose(inner)) == (expected_matrix, expected_origin)
            else:
                with pytest.raises(GeometryError, match="signed 64-bit"):
                    outer.compose(inner)


@pytest.mark.parametrize(
    "values",
    (
        (INT64_MIN - 1, 0, 0, 1),
        (0, INT64_MIN - 1, 1, 0),
        (0, 0, INT64_MAX + 1, 1),
        (0, 0, 1, INT64_MAX + 1),
        (Fraction(1, 3), 0, 1, 1),
        (0, Fraction(1, 3), 1, 1),
        (0, 0, Fraction(4, 3), 2),
        (0, 0, 1, Fraction(4, 3)),
        (Fraction(2 * INT64_MIN - 1, 2), 0, 0, 1),
        (0, Fraction(2 * INT64_MIN - 1, 2), 1, 0),
        (0, 0, Fraction(2 * INT64_MAX + 1, 2), 1),
        (0, 0, 1, Fraction(2 * INT64_MAX + 1, 2)),
    ),
)
def test_bounds_reject_values_outside_signed64_half_grid(values: BoundValues) -> None:
    """A public bound has the same finite half-grid contract as generated bounds."""
    left, bottom, right, top = values
    with pytest.raises(GeometryError):
        Bounds(left, bottom, right, top)


def _subtract(left: Pair, right: Pair) -> Pair:
    return left[0] - right[0], left[1] - right[1]


def _determinant(left: Pair, right: Pair) -> int:
    return left[0] * right[1] - left[1] * right[0]


def _intervals_overlap(a: int, b: int, c: int, d: int) -> bool:
    return max(min(a, b), min(c, d)) <= min(max(a, b), max(c, d))


def _segments_intersect(a: Pair, b: Pair, c: Pair, d: Pair) -> bool:
    direction_ab = _subtract(b, a)
    direction_cd = _subtract(d, c)
    displacement = _subtract(c, a)
    denominator = _determinant(direction_ab, direction_cd)
    if denominator:
        along_ab = Fraction(_determinant(displacement, direction_cd), denominator)
        along_cd = Fraction(_determinant(displacement, direction_ab), denominator)
        return 0 <= along_ab <= 1 and 0 <= along_cd <= 1
    if _determinant(displacement, direction_ab):
        return False
    return _intervals_overlap(a[0], b[0], c[0], d[0]) and _intervals_overlap(a[1], b[1], c[1], d[1])


def _is_simple(vertices: tuple[Pair, ...]) -> bool:
    count = len(vertices)
    if len(set(vertices)) != count:
        return False
    for index in range(count):
        a, b, c = vertices[index], vertices[(index + 1) % count], vertices[(index + 2) % count]
        incoming, outgoing = _subtract(b, a), _subtract(c, b)
        if (
            _determinant(incoming, outgoing) == 0
            and incoming[0] * outgoing[0] + incoming[1] * outgoing[1] < 0
        ):
            return False
        for other in range(index + 2, count):
            if index == 0 and other == count - 1:
                continue
            if _segments_intersect(a, b, vertices[other], vertices[(other + 1) % count]):
                return False
    # This is only a degeneracy classifier; area validation below uses Pick's theorem.
    turns = {
        _determinant(
            _subtract(vertices[(index + 1) % count], vertices[index]),
            _subtract(vertices[(index + 2) % count], vertices[(index + 1) % count]),
        )
        for index in range(count)
    }
    return turns != {0}


def _on_segment(a: Pair, b: Pair, point: Pair) -> bool:
    return (
        _determinant(_subtract(b, a), _subtract(point, a)) == 0
        and _intervals_overlap(a[0], b[0], point[0], point[0])
        and _intervals_overlap(a[1], b[1], point[1], point[1])
    )


def _ray_contains(vertices: tuple[Pair, ...], point: Pair, *, boundary: bool) -> bool:
    parity = False
    for a, b in zip(vertices, (*vertices[1:], vertices[0]), strict=True):
        if _on_segment(a, b, point):
            return boundary
        if (a[1] <= point[1] < b[1]) or (b[1] <= point[1] < a[1]):
            crossing_x = Fraction(a[0]) + Fraction(point[1] - a[1], b[1] - a[1]) * (b[0] - a[0])
            if crossing_x > point[0]:
                parity = not parity
    return parity


def _pick_area(vertices: tuple[Pair, ...]) -> Fraction:
    minimum_x = min(point[0] for point in vertices)
    maximum_x = max(point[0] for point in vertices)
    minimum_y = min(point[1] for point in vertices)
    maximum_y = max(point[1] for point in vertices)
    interior = sum(
        _ray_contains(vertices, (x, y), boundary=False)
        for x in range(minimum_x, maximum_x + 1)
        for y in range(minimum_y, maximum_y + 1)
    )
    boundary_points = sum(
        math.gcd(abs(b[0] - a[0]), abs(b[1] - a[1]))
        for a, b in zip(vertices, (*vertices[1:], vertices[0]), strict=True)
    )
    return Fraction(interior - 1) + Fraction(boundary_points, 2)


def test_polygon_simplicity_area_and_winding_match_independent_oracles() -> None:
    generator = random.Random(0x5A17C0DE)
    grid: tuple[Pair, ...] = tuple((x, y) for x in range(-2, 3) for y in range(-2, 3))
    accepted = rejected = 0

    for _ in range(2_000):
        vertices = tuple(generator.sample(grid, generator.randrange(3, 9)))
        expected_simple = _is_simple(vertices)
        if not expected_simple:
            rejected += 1
            with pytest.raises(GeometryError):
                Polygon(tuple(Point(*point) for point in vertices))
            continue

        accepted += 1
        polygon = Polygon(tuple(Point(*point) for point in vertices))
        assert polygon.area == _pick_area(vertices)
        for probe in grid:
            assert polygon.contains(Point(*probe)) == _ray_contains(vertices, probe, boundary=True)
            assert polygon.contains(Point(*probe), boundary=False) == _ray_contains(
                vertices, probe, boundary=False
            )

    assert accepted > 400
    assert rejected > 1_000
