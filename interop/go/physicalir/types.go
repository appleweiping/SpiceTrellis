// Package physicalir is a dependency-free Go consumer for SpiceTrellis
// physical-library JSON version 1.
//
// It preserves exact declaration order and integer database units. Decode
// rejects ambiguous JSON and validates geometry, references, hierarchy, and
// resource limits before returning a Library.
package physicalir

import "math/big"

const (
	Schema               = "org.spicetrellis.physical-library"
	SchemaVersion        = 1
	MaxBytes             = 8 * 1024 * 1024
	MaxDefinitions       = 4_096
	MaxItems             = 100_000
	MaxTextRunes         = 4_096
	MaxDepth             = 64
	MaxStoredPoints      = 1_000_000
	MaxStoredPolygonWork = 10_000_000
	MaxLibraryTextBytes  = 8 * 1024 * 1024
	MaxPolygonVertices   = 1_024
	MaxPathPoints        = 100_000
)

// DistanceUnit is the database-unit scale declared by a physical library.
type DistanceUnit string

const (
	Micrometer DistanceUnit = "um"
	Nanometer  DistanceUnit = "nm"
	Angstrom   DistanceUnit = "angstrom"
)

// LayerPurpose is a semantic label, not a conductivity or DRC assertion.
type LayerPurpose string

const (
	PurposeUnknown     LayerPurpose = "unknown"
	PurposeDrawing     LayerPurpose = "drawing"
	PurposePin         LayerPurpose = "pin"
	PurposeLabel       LayerPurpose = "label"
	PurposeObstruction LayerPurpose = "obstruction"
	PurposeOutline     LayerPurpose = "outline"
)

// Point is one exact signed-64 database-unit coordinate pair.
type Point struct {
	X int64
	Y int64
}

// Rectangle is an origin plus positive dimensions. Its far edges remain int64.
type Rectangle struct {
	Origin Point
	Width  int64
	Height int64
}

// Polygon is a simple polygon without holes; closure is implicit.
type Polygon struct {
	Vertices []Point
}

// Path is a constant-width centerline with conservative square-cap bounds.
type Path struct {
	Points []Point
	Width  int64
}

// GeometryKind identifies the single populated variant in Geometry.
type GeometryKind string

const (
	RectangleKind GeometryKind = "rectangle"
	PolygonKind   GeometryKind = "polygon"
	PathKind      GeometryKind = "path"
)

// Geometry is a closed tagged union. Exactly one matching pointer is non-nil.
type Geometry struct {
	Kind      GeometryKind
	Rectangle *Rectangle
	Polygon   *Polygon
	Path      *Path
}

// Shape retains a technology layer, optional cell-local net label and geometry.
type Shape struct {
	Layer    string
	Net      *string
	Geometry Geometry
}

// Transform reflects in local x, rotates clockwise, then translates.
type Transform struct {
	Origin    Point
	Clockwise int
	ReflectX  bool
}

// Instance places one referenced physical cell.
type Instance struct {
	Name      string
	Cell      string
	Transform Transform
}

// Annotation is non-connective text at an exact point.
type Annotation struct {
	Text string
	At   Point
}

// Layout is a raw materializable view.
type Layout struct {
	Shapes      []Shape
	Instances   []Instance
	Annotations []Annotation
}

// Port groups explicit access shapes in an abstract view.
type Port struct {
	Name   string
	Shapes []Shape
}

// Abstract retains outline, port access and blockage geometry.
type Abstract struct {
	Outline   Polygon
	Ports     []Port
	Blockages []Shape
}

// Cell may independently carry layout, abstract and circuit cross-view data.
type Cell struct {
	Name          string
	Ports         []string
	Layout        *Layout
	Abstract      *Abstract
	CircuitModule *string
}

// Layer is one uniquely named numeric layer/datatype pair.
type Layer struct {
	ID          string
	Number      uint64
	Datatype    uint64
	Purpose     LayerPurpose
	Description string
}

// Technology is the exact ordered layer map and package list.
type Technology struct {
	Name     string
	Layers   []Layer
	Packages []string
}

// Library is the complete validated physical-library v1 document.
type Library struct {
	Schema     string
	Version    int
	Name       string
	Unit       DistanceUnit
	Technology Technology
	Cells      []Cell
}

// SignedDoubleArea returns a fresh exact integer. It cannot overflow on int64 vertices.
func (polygon Polygon) SignedDoubleArea() *big.Int {
	return signedDoubleArea(polygon.Vertices)
}

// Area returns a fresh non-negative exact rational, including half-unit areas.
func (polygon Polygon) Area() *big.Rat {
	doubled := polygon.SignedDoubleArea()
	doubled.Abs(doubled)
	return new(big.Rat).SetFrac(doubled, big.NewInt(2))
}

// Contains applies exact winding-number containment with an explicit boundary policy.
func (polygon Polygon) Contains(point Point, boundary bool) bool {
	return polygonContains(polygon.Vertices, point, boundary)
}

// Area returns a fresh exact integer. Width*height cannot overflow it.
func (rectangle Rectangle) Area() *big.Int {
	return new(big.Int).Mul(big.NewInt(rectangle.Width), big.NewInt(rectangle.Height))
}
