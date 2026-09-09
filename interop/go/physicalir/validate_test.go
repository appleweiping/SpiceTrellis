package physicalir

import (
	"fmt"
	"math"
	"strings"
	"testing"
)

func stringPointer(value string) *string { return &value }

func basicLibrary() Library {
	return Library{
		Schema:  Schema,
		Version: SchemaVersion,
		Name:    "basic",
		Unit:    Nanometer,
		Technology: Technology{
			Name: "tech",
			Layers: []Layer{{
				ID: "wire", Number: 1, Datatype: 0, Purpose: PurposeDrawing,
			}},
			Packages: []string{},
		},
		Cells: []Cell{{
			Name:  "leaf",
			Ports: []string{"p"},
			Layout: &Layout{
				Shapes: []Shape{{
					Layer: "wire", Net: stringPointer("p"),
					Geometry: Geometry{
						Kind: RectangleKind,
						Rectangle: &Rectangle{
							Origin: Point{0, 0}, Width: 2, Height: 3,
						},
					},
				}},
				Instances:   []Instance{},
				Annotations: []Annotation{{Text: "note", At: Point{1, 1}}},
			},
			Abstract: &Abstract{
				Outline: Polygon{Vertices: []Point{{0, 0}, {4, 0}, {4, 4}, {0, 4}}},
				Ports: []Port{{
					Name: "p",
					Shapes: []Shape{{
						Layer: "wire", Net: stringPointer("p"),
						Geometry: Geometry{Kind: PathKind, Path: &Path{
							Points: []Point{{1, 1}, {2, 1}}, Width: 1,
						}},
					}},
				}},
				Blockages: []Shape{},
			},
		}},
	}
}

func TestBasicInMemoryLibraryIsValid(t *testing.T) {
	library := basicLibrary()
	if err := library.Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestGeometryValidationRejectsEveryUnsupportedBoundary(t *testing.T) {
	tests := []struct {
		name    string
		message string
		value   Geometry
	}{
		{"unknown", "unknown geometry", Geometry{}},
		{"missing rectangle", "union is inconsistent", Geometry{Kind: RectangleKind}},
		{"mixed union", "union is inconsistent", Geometry{Kind: RectangleKind, Rectangle: &Rectangle{Point{}, 1, 1}, Path: &Path{}}},
		{"zero width", "dimensions must be positive", Geometry{Kind: RectangleKind, Rectangle: &Rectangle{Point{}, 0, 1}}},
		{"rectangle overflow", "edge exceeds", Geometry{Kind: RectangleKind, Rectangle: &Rectangle{Point{math.MaxInt64, 0}, 1, 1}}},
		{"short polygon", "3..", Geometry{Kind: PolygonKind, Polygon: &Polygon{Vertices: []Point{{0, 0}, {1, 0}}}}},
		{"duplicate polygon", "duplicated", Geometry{Kind: PolygonKind, Polygon: &Polygon{Vertices: []Point{{0, 0}, {1, 0}, {0, 0}}}}},
		{"backtrack polygon", "backtrack", Geometry{Kind: PolygonKind, Polygon: &Polygon{Vertices: []Point{{0, 0}, {3, 0}, {1, 0}, {0, 1}}}}},
		{"crossing polygon", "intersect", Geometry{Kind: PolygonKind, Polygon: &Polygon{Vertices: []Point{{0, 0}, {2, 2}, {0, 2}, {2, 0}}}}},
		{"zero area polygon", "backtrack", Geometry{Kind: PolygonKind, Polygon: &Polygon{Vertices: []Point{{0, 0}, {1, 0}, {2, 0}}}}},
		{"short path", "2..", Geometry{Kind: PathKind, Path: &Path{Points: []Point{{0, 0}}, Width: 1}}},
		{"zero path width", "width must be positive", Geometry{Kind: PathKind, Path: &Path{Points: []Point{{0, 0}, {1, 0}}, Width: 0}}},
		{"duplicate path point", "are equal", Geometry{Kind: PathKind, Path: &Path{Points: []Point{{0, 0}, {0, 0}}, Width: 1}}},
		{"path low envelope", "envelope exceeds", Geometry{Kind: PathKind, Path: &Path{Points: []Point{{math.MinInt64, 0}, {0, 0}}, Width: 1}}},
		{"path high envelope", "envelope exceeds", Geometry{Kind: PathKind, Path: &Path{Points: []Point{{0, 0}, {math.MaxInt64, 0}}, Width: 1}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := validateGeometry(test.value, "test"); err == nil || !strings.Contains(err.Error(), test.message) {
				t.Fatalf("error = %v, want %q", err, test.message)
			}
		})
	}
}

func TestExactIntersectionAreaAndWindingOnConcavePolygon(t *testing.T) {
	polygon := Polygon{Vertices: []Point{{0, 0}, {5, 0}, {5, 2}, {2, 2}, {2, 5}, {0, 5}}}
	if err := validatePolygon(polygon, "test"); err != nil {
		t.Fatal(err)
	}
	if got := polygon.SignedDoubleArea().String(); got != "32" {
		t.Fatalf("signed double area = %s", got)
	}
	if !polygon.Contains(Point{1, 1}, true) || polygon.Contains(Point{3, 3}, true) ||
		!polygon.Contains(Point{2, 3}, true) || polygon.Contains(Point{2, 3}, false) {
		t.Fatal("concave winding or boundary policy differs from exact oracle")
	}
	reversed := Polygon{Vertices: []Point{{0, 5}, {2, 5}, {2, 2}, {5, 2}, {5, 0}, {0, 0}}}
	if reversed.SignedDoubleArea().String() != "-32" || !reversed.Contains(Point{1, 1}, true) {
		t.Fatal("clockwise polygon changed area or containment")
	}
	if !segmentsIntersect(
		Point{math.MinInt64, math.MinInt64}, Point{math.MaxInt64, math.MaxInt64},
		Point{math.MinInt64, math.MaxInt64}, Point{math.MaxInt64, math.MinInt64},
	) {
		t.Fatal("extreme crossing was lost to overflow")
	}
}

func TestSemanticReferencesIdentitiesAndViewsAreStrict(t *testing.T) {
	tests := []struct {
		name    string
		message string
		mutate  func(*Library)
	}{
		{"schema", "unsupported schema", func(value *Library) { value.Schema = "other" }},
		{"unit", "distance unit", func(value *Library) { value.Unit = "meter" }},
		{"purpose", "layer purpose", func(value *Library) { value.Technology.Layers[0].Purpose = "metal" }},
		{"duplicate package", "duplicates", func(value *Library) { value.Technology.Packages = []string{"p", "p"} }},
		{"duplicate layer id", "duplicates layer", func(value *Library) {
			value.Technology.Layers = append(value.Technology.Layers, value.Technology.Layers[0])
		}},
		{"duplicate numeric layer", "numeric layer", func(value *Library) {
			value.Technology.Layers = append(value.Technology.Layers, Layer{ID: "other", Number: 1, Datatype: 1, Purpose: PurposePin})
		}},
		{"duplicate cell", "duplicates cell", func(value *Library) { value.Cells = append(value.Cells, value.Cells[0]) }},
		{"empty cell", "requires layout", func(value *Library) { value.Cells[0].Layout, value.Cells[0].Abstract = nil, nil }},
		{"duplicate cell port", "duplicates", func(value *Library) { value.Cells[0].Ports = []string{"p", "p"} }},
		{"missing layer", "undeclared", func(value *Library) { value.Cells[1].Layout.Shapes[0].Layer = "missing" }},
		{"bad abstract port", "cell interface", func(value *Library) { value.Cells[1].Abstract.Ports[0].Name = "q" }},
		{"empty abstract shapes", "requires 1", func(value *Library) { value.Cells[1].Abstract.Ports[0].Shapes = nil }},
		{"port net mismatch", "does not match", func(value *Library) { value.Cells[1].Abstract.Ports[0].Shapes[0].Net = stringPointer("q") }},
		{"duplicate abstract port", "duplicates an abstract", func(value *Library) {
			value.Cells[1].Abstract.Ports = append(value.Cells[1].Abstract.Ports, value.Cells[1].Abstract.Ports[0])
		}},
		{"bad transform", "orientation", func(value *Library) {
			value.Cells[0].Layout.Instances = []Instance{{Name: "x", Cell: "leaf", Transform: Transform{Clockwise: 45}}}
		}},
		{"unknown cell", "unknown cell", func(value *Library) { value.Cells[0].Layout.Instances = []Instance{{Name: "x", Cell: "missing"}} }},
		{"duplicate instance", "duplicates an instance", func(value *Library) {
			value.Cells[0].Layout.Instances = []Instance{{Name: "x", Cell: "leaf"}, {Name: "x", Cell: "leaf"}}
		}},
		{"control text", "control", func(value *Library) { value.Cells[0].Name = "bad\x7f" }},
		{"long text", "character limit", func(value *Library) { value.Name = strings.Repeat("x", MaxTextRunes+1) }},
		{"invalid geometry union", "payload is absent", func(value *Library) { value.Cells[1].Layout.Shapes[0].Geometry.Rectangle = nil }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := fixtureLibrary(t)
			test.mutate(&value)
			if err := value.Validate(); err == nil || !strings.Contains(err.Error(), test.message) {
				t.Fatalf("error = %v, want substring %q", err, test.message)
			}
		})
	}
}

func TestHierarchyCyclesAndLongestPathsAreBounded(t *testing.T) {
	cycle := basicLibrary()
	cycle.Cells[0].Layout.Instances = []Instance{{Name: "self", Cell: "leaf"}}
	if err := cycle.Validate(); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("cycle error = %v", err)
	}

	chain := basicLibrary()
	chain.Cells = make([]Cell, MaxDepth)
	for index := range chain.Cells {
		chain.Cells[index] = Cell{Name: fmt.Sprintf("c%d", index), Layout: &Layout{}}
		if index > 0 {
			chain.Cells[index].Layout.Instances = []Instance{{Name: "child", Cell: chain.Cells[index-1].Name}}
		}
	}
	if err := chain.Validate(); err != nil {
		t.Fatalf("depth %d rejected: %v", MaxDepth, err)
	}
	chain.Cells = append(chain.Cells, Cell{
		Name: "overflow", Layout: &Layout{Instances: []Instance{{Name: "child", Cell: chain.Cells[len(chain.Cells)-1].Name}}},
	})
	if err := chain.Validate(); err == nil || !strings.Contains(err.Error(), "depth") {
		t.Fatalf("depth overflow error = %v", err)
	}
}

func TestResourceBudgetsAreInclusiveAndFailClosed(t *testing.T) {
	budget := &resourceBudget{
		items: MaxItems - 1, points: MaxStoredPoints - 1,
		polygonWork: MaxStoredPolygonWork - 1, textBytes: MaxLibraryTextBytes - 1,
	}
	if err := budget.add("test", 1, 1, 1); err != nil {
		t.Fatalf("exact resource limits rejected: %v", err)
	}
	for _, test := range []struct {
		name   string
		budget resourceBudget
		add    [3]int
	}{
		{"items", resourceBudget{items: MaxItems}, [3]int{1, 0, 0}},
		{"points", resourceBudget{points: MaxStoredPoints}, [3]int{0, 1, 0}},
		{"work", resourceBudget{polygonWork: MaxStoredPolygonWork}, [3]int{0, 0, 1}},
	} {
		t.Run(test.name, func(t *testing.T) {
			if err := test.budget.add("test", test.add[0], test.add[1], test.add[2]); err == nil {
				t.Fatal("resource overflow accepted")
			}
		})
	}
	if err := budget.text("x", "test", false); err != nil {
		t.Fatalf("exact text limit rejected: %v", err)
	}
	if err := budget.text("x", "test", false); err == nil {
		t.Fatal("text overflow accepted")
	}
}

func TestStoredPointBudgetCountsAcrossOtherwiseValidPaths(t *testing.T) {
	points := make([]Point, MaxPathPoints)
	for index := range points {
		points[index] = Point{int64(index), 0}
	}
	path := &Path{Points: points, Width: 1}
	shape := Shape{Layer: "wire", Geometry: Geometry{Kind: PathKind, Path: path}}
	library := basicLibrary()
	library.Cells[0].Abstract = nil
	library.Cells[0].Ports = nil
	library.Cells[0].Layout.Shapes = make([]Shape, 11)
	for index := range library.Cells[0].Layout.Shapes {
		library.Cells[0].Layout.Shapes[index] = shape
	}
	if err := library.Validate(); err == nil || !strings.Contains(err.Error(), "stored points") {
		t.Fatalf("aggregate point error = %v", err)
	}
}
