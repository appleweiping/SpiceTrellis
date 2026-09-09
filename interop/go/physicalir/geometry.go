package physicalir

import (
	"fmt"
	"math"
	"math/big"
)

func integer(value int64) *big.Int {
	return big.NewInt(value)
}

func difference(left, right int64) *big.Int {
	return new(big.Int).Sub(integer(left), integer(right))
}

func cross(a, b, c Point) *big.Int {
	abX := difference(b.X, a.X)
	abY := difference(b.Y, a.Y)
	acX := difference(c.X, a.X)
	acY := difference(c.Y, a.Y)
	left := new(big.Int).Mul(abX, acY)
	right := new(big.Int).Mul(abY, acX)
	return new(big.Int).Sub(left, right)
}

func between(a, b, point Point) bool {
	return min(a.X, b.X) <= point.X && point.X <= max(a.X, b.X) &&
		min(a.Y, b.Y) <= point.Y && point.Y <= max(a.Y, b.Y)
}

func segmentsIntersect(a, b, c, d Point) bool {
	abC := cross(a, b, c).Sign()
	abD := cross(a, b, d).Sign()
	cdA := cross(c, d, a).Sign()
	cdB := cross(c, d, b).Sign()
	if abC*abD < 0 && cdA*cdB < 0 {
		return true
	}
	return abC == 0 && between(a, b, c) ||
		abD == 0 && between(a, b, d) ||
		cdA == 0 && between(c, d, a) ||
		cdB == 0 && between(c, d, b)
}

func signedDoubleArea(vertices []Point) *big.Int {
	total := new(big.Int)
	if len(vertices) == 0 {
		return total
	}
	for index, a := range vertices {
		b := vertices[(index+1)%len(vertices)]
		left := new(big.Int).Mul(integer(a.X), integer(b.Y))
		right := new(big.Int).Mul(integer(b.X), integer(a.Y))
		total.Add(total, new(big.Int).Sub(left, right))
	}
	return total
}

func polygonContains(vertices []Point, point Point, boundary bool) bool {
	winding := 0
	for index, a := range vertices {
		b := vertices[(index+1)%len(vertices)]
		turn := cross(a, b, point).Sign()
		if turn == 0 && between(a, b, point) {
			return boundary
		}
		if a.Y <= point.Y && point.Y < b.Y && turn > 0 {
			winding++
		} else if b.Y <= point.Y && point.Y < a.Y && turn < 0 {
			winding--
		}
	}
	return winding != 0
}

func validateRectangle(rectangle Rectangle, location string) error {
	if rectangle.Width <= 0 || rectangle.Height <= 0 {
		return validationError(location, "rectangle dimensions must be positive")
	}
	right := new(big.Int).Add(integer(rectangle.Origin.X), integer(rectangle.Width))
	top := new(big.Int).Add(integer(rectangle.Origin.Y), integer(rectangle.Height))
	if !right.IsInt64() || !top.IsInt64() {
		return validationError(location, "rectangle edge exceeds signed 64-bit range")
	}
	return nil
}

func validatePolygon(polygon Polygon, location string) error {
	vertices := polygon.Vertices
	if len(vertices) < 3 || len(vertices) > MaxPolygonVertices {
		return validationError(location, "polygon requires 3..%d vertices", MaxPolygonVertices)
	}
	seen := make(map[Point]struct{}, len(vertices))
	for index, point := range vertices {
		if _, exists := seen[point]; exists {
			return validationError(location, "polygon vertex %d is duplicated", index)
		}
		seen[point] = struct{}{}
	}
	for index, a := range vertices {
		b := vertices[(index+1)%len(vertices)]
		c := vertices[(index+2)%len(vertices)]
		if cross(a, b, c).Sign() == 0 && !between(a, c, b) {
			return validationError(location, "adjacent polygon edges backtrack at vertex %d", index+1)
		}
		for other := index + 2; other < len(vertices); other++ {
			if index == 0 && other == len(vertices)-1 {
				continue
			}
			if segmentsIntersect(a, b, vertices[other], vertices[(other+1)%len(vertices)]) {
				return validationError(location, "polygon edges %d and %d intersect non-adjacently", index, other)
			}
		}
	}
	if signedDoubleArea(vertices).Sign() == 0 {
		return validationError(location, "polygon area must be non-zero")
	}
	return nil
}

func validatePath(path Path, location string) error {
	if len(path.Points) < 2 || len(path.Points) > MaxPathPoints {
		return validationError(location, "path requires 2..%d points", MaxPathPoints)
	}
	if path.Width <= 0 {
		return validationError(location, "path width must be positive")
	}
	minimumX, maximumX := path.Points[0].X, path.Points[0].X
	minimumY, maximumY := path.Points[0].Y, path.Points[0].Y
	for index, point := range path.Points {
		if index > 0 && point == path.Points[index-1] {
			return validationError(location, "consecutive path points %d and %d are equal", index-1, index)
		}
		minimumX, maximumX = min(minimumX, point.X), max(maximumX, point.X)
		minimumY, maximumY = min(minimumY, point.Y), max(maximumY, point.Y)
	}
	width := integer(path.Width)
	twiceMinimum := integer(math.MinInt64)
	twiceMinimum.Mul(twiceMinimum, big.NewInt(2))
	twiceMaximum := integer(math.MaxInt64)
	twiceMaximum.Mul(twiceMaximum, big.NewInt(2))
	left := new(big.Int).Sub(new(big.Int).Mul(integer(minimumX), big.NewInt(2)), width)
	bottom := new(big.Int).Sub(new(big.Int).Mul(integer(minimumY), big.NewInt(2)), width)
	right := new(big.Int).Add(new(big.Int).Mul(integer(maximumX), big.NewInt(2)), width)
	top := new(big.Int).Add(new(big.Int).Mul(integer(maximumY), big.NewInt(2)), width)
	if left.Cmp(twiceMinimum) < 0 || bottom.Cmp(twiceMinimum) < 0 ||
		right.Cmp(twiceMaximum) > 0 || top.Cmp(twiceMaximum) > 0 {
		return validationError(location, "path envelope exceeds signed 64-bit range")
	}
	return nil
}

func validateGeometry(geometry Geometry, location string) error {
	switch geometry.Kind {
	case RectangleKind:
		if geometry.Rectangle == nil || geometry.Polygon != nil || geometry.Path != nil {
			return validationError(location, "rectangle geometry union is inconsistent")
		}
		return validateRectangle(*geometry.Rectangle, location)
	case PolygonKind:
		if geometry.Rectangle != nil || geometry.Polygon == nil || geometry.Path != nil {
			return validationError(location, "polygon geometry union is inconsistent")
		}
		return validatePolygon(*geometry.Polygon, location)
	case PathKind:
		if geometry.Rectangle != nil || geometry.Polygon != nil || geometry.Path == nil {
			return validationError(location, "path geometry union is inconsistent")
		}
		return validatePath(*geometry.Path, location)
	default:
		return validationError(location, "unknown geometry kind %q", geometry.Kind)
	}
}

func geometryCost(geometry Geometry) (int, int, error) {
	switch geometry.Kind {
	case RectangleKind:
		if geometry.Rectangle == nil {
			return 0, 0, fmt.Errorf("rectangle payload is absent")
		}
		return 4, 16, nil
	case PolygonKind:
		if geometry.Polygon == nil {
			return 0, 0, fmt.Errorf("polygon payload is absent")
		}
		count := len(geometry.Polygon.Vertices)
		return count, count * count, nil
	case PathKind:
		if geometry.Path == nil {
			return 0, 0, fmt.Errorf("path payload is absent")
		}
		return len(geometry.Path.Points), 0, nil
	default:
		return 0, 0, fmt.Errorf("unknown geometry kind %q", geometry.Kind)
	}
}
