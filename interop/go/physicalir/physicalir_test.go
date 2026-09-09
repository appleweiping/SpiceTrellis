package physicalir

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"
)

func fixturePath(name string) string {
	return filepath.Join("..", "..", "fixtures", "physical-v1", name)
}

func fixtureBytes(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(fixturePath(name))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func fixtureLibrary(t *testing.T) Library {
	t.Helper()
	library, err := DecodeBytes(fixtureBytes(t, "rich-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	return library
}

func TestRichFixtureCoversTheCompleteWireShape(t *testing.T) {
	library, err := Load(fixturePath("rich-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	if library.Schema != Schema || library.Version != SchemaVersion ||
		library.Name != "interop-library" || library.Unit != Nanometer {
		t.Fatalf("unexpected header: %#v", library)
	}
	if got := library.Technology.Layers[0].Number; got != math.MaxUint64 {
		t.Fatalf("uint64 maximum decoded as %d", got)
	}
	if got := len(library.Cells); got != 2 {
		t.Fatalf("cell count = %d", got)
	}
	top, leaf := library.Cells[0], library.Cells[1]
	if top.Layout == nil || len(top.Layout.Instances) != 1 ||
		top.Layout.Instances[0].Transform != (Transform{Point{20, 30}, 90, true}) {
		t.Fatalf("top layout was not retained: %#v", top.Layout)
	}
	if leaf.Layout == nil || leaf.Abstract == nil || leaf.CircuitModule == nil ||
		*leaf.CircuitModule != "circuit/leaf" || len(leaf.Layout.Shapes) != 3 {
		t.Fatalf("leaf views were not retained: %#v", leaf)
	}
	if leaf.Layout.Shapes[0].Geometry.Rectangle == nil ||
		leaf.Layout.Shapes[1].Geometry.Polygon == nil ||
		leaf.Layout.Shapes[2].Geometry.Path == nil {
		t.Fatal("geometry variants were not decoded")
	}
	if leaf.Layout.Shapes[1].Geometry.Polygon.Area().Cmp(big.NewRat(2, 1)) != 0 {
		t.Fatalf("triangle area = %s", leaf.Layout.Shapes[1].Geometry.Polygon.Area())
	}
	if leaf.Abstract.Ports[0].Shapes[0].Net == nil ||
		*leaf.Abstract.Ports[0].Shapes[0].Net != "p" {
		t.Fatal("abstract port label was not retained")
	}

	decoded, err := Decode(bytes.NewReader(fixtureBytes(t, "rich-v1.json")))
	if err != nil || decoded.Name != library.Name {
		t.Fatalf("reader decode = %#v, %v", decoded, err)
	}
}

func TestExtremePolygonUsesBigIntegerGeometry(t *testing.T) {
	library, err := Load(fixturePath("extreme-polygon-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	polygon := library.Cells[0].Layout.Shapes[0].Geometry.Polygon
	wantDouble, ok := new(big.Int).SetString("680564733841876926852962238568698216450", 10)
	if !ok {
		t.Fatal("invalid test oracle")
	}
	if got := polygon.SignedDoubleArea(); got.Cmp(wantDouble) != 0 {
		t.Fatalf("signed double area = %s, want %s", got, wantDouble)
	}
	wantArea, ok := new(big.Int).SetString("340282366920938463426481119284349108225", 10)
	if !ok || polygon.Area().Cmp(new(big.Rat).SetInt(wantArea)) != 0 {
		t.Fatalf("area = %s", polygon.Area())
	}
	if !polygon.Contains(Point{0, 0}, true) || !polygon.Contains(Point{math.MinInt64, 0}, true) ||
		polygon.Contains(Point{math.MinInt64, 0}, false) {
		t.Fatal("exact extreme winding or boundary policy is wrong")
	}
	rectangle := Rectangle{Point{math.MinInt64, math.MinInt64}, math.MaxInt64, math.MaxInt64}
	if got, want := rectangle.Area(), new(big.Int).Mul(big.NewInt(math.MaxInt64), big.NewInt(math.MaxInt64)); got.Cmp(want) != 0 {
		t.Fatalf("rectangle area = %s, want %s", got, want)
	}
}

func replaceOnce(t *testing.T, input, old, replacement string) []byte {
	t.Helper()
	output := strings.Replace(input, old, replacement, 1)
	if output == input {
		t.Fatalf("mutation source %q not found", old)
	}
	return []byte(output)
}

func TestStrictDecoderRejectsAmbiguousAndMistypedWireData(t *testing.T) {
	valid := string(fixtureBytes(t, "rich-v1.json"))
	tests := map[string][]byte{
		"duplicate root":    replaceOnce(t, valid, `"version": 1,`, `"version": 1, "version": 1,`),
		"escaped duplicate": replaceOnce(t, valid, `"name": "top",`, `"name": "top", "na\u006de": "top",`),
		"unknown root":      replaceOnce(t, valid, `"version": 1,`, `"version": 1, "extra": true,`),
		"unknown geometry":  replaceOnce(t, valid, `"kind": "rectangle",`, `"kind": "rectangle", "extra": 0,`),
		"missing version": replaceOnce(t, valid, `  "version": 1,
`, ""),
		"float version":         replaceOnce(t, valid, `"version": 1`, `"version": 1.0`),
		"exponent angle":        replaceOnce(t, valid, `"clockwise": 90`, `"clockwise": 9e1`),
		"coordinate number":     replaceOnce(t, valid, `"origin": ["20", "30"]`, `"origin": [20, "30"]`),
		"negative zero":         replaceOnce(t, valid, `"origin": ["20", "30"]`, `"origin": ["-0", "30"]`),
		"leading zero":          replaceOnce(t, valid, `"origin": ["20", "30"]`, `"origin": ["020", "30"]`),
		"unicode digit":         replaceOnce(t, valid, `"origin": ["20", "30"]`, `"origin": ["٢", "30"]`),
		"signed overflow":       replaceOnce(t, valid, `"origin": ["20", "30"]`, `"origin": ["9223372036854775808", "30"]`),
		"unsigned overflow":     replaceOnce(t, valid, `"number": "18446744073709551615"`, `"number": "18446744073709551616"`),
		"unknown geometry kind": replaceOnce(t, valid, `"kind": "path"`, `"kind": "circle"`),
		"null array":            replaceOnce(t, valid, `"ports": [],`, `"ports": null,`),
		"trailing value":        []byte(valid + `{}`),
		"lone high surrogate":   replaceOnce(t, valid, `"interop-library"`, `"\ud800"`),
		"lone low surrogate":    replaceOnce(t, valid, `"interop-library"`, `"\udc00"`),
		"bad surrogate pair":    replaceOnce(t, valid, `"interop-library"`, `"\ud800\u0041"`),
	}
	for name, input := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeBytes(input); err == nil {
				t.Fatal("invalid input was accepted")
			}
		})
	}
	if _, err := DecodeBytes([]byte{0xff}); err == nil || !strings.Contains(err.Error(), "UTF-8") {
		t.Fatalf("invalid UTF-8 error = %v", err)
	}
	if _, err := DecodeBytes(nil); err == nil {
		t.Fatal("empty input accepted")
	}
}

func TestSharedPhysicalRejectionCorpus(t *testing.T) {
	var corpus struct {
		Schema  string `json:"schema"`
		Version int    `json:"version"`
		Cases   []struct {
			Name string `json:"name"`
			Old  string `json:"old"`
			New  string `json:"new"`
		} `json:"cases"`
	}
	data := fixtureBytes(t, "rejection-corpus-v1.json")
	if err := json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	if corpus.Schema != "org.spicetrellis.physical-library-rejection-corpus" ||
		corpus.Version != 1 || len(corpus.Cases) < 20 {
		t.Fatalf("unexpected rejection corpus header: %#v", corpus)
	}
	valid := string(fixtureBytes(t, "rich-v1.json"))
	for _, test := range corpus.Cases {
		t.Run(test.Name, func(t *testing.T) {
			mutated := strings.Replace(valid, test.Old, test.New, 1)
			if mutated == valid {
				t.Fatalf("replacement source %q was not found", test.Old)
			}
			if _, err := DecodeBytes([]byte(mutated)); err == nil {
				t.Fatal("shared invalid document was accepted")
			}
		})
	}
}

func TestUnicodeReplacementAndValidSurrogatePairStayDistinct(t *testing.T) {
	valid := string(fixtureBytes(t, "rich-v1.json"))
	for name, replacement := range map[string]string{
		"literal replacement": "�",
		"escaped replacement": `\ufffd`,
		"valid pair":          `\ud83d\ude00`,
	} {
		t.Run(name, func(t *testing.T) {
			library, err := DecodeBytes(replaceOnce(t, valid, `interop-library`, replacement))
			if err != nil {
				t.Fatal(err)
			}
			if !utf8.ValidString(library.Name) || utf8.RuneCountInString(library.Name) != 1 {
				t.Fatalf("decoded name = %q", library.Name)
			}
		})
	}
	library, err := DecodeBytes(replaceOnce(t, valid, `interop-library`, `\\ud800`))
	if err != nil {
		t.Fatal(err)
	}
	if library.Name != `\ud800` {
		t.Fatalf("escaped backslash text changed to %q", library.Name)
	}
}

func TestReaderBoundsDepthAndErrors(t *testing.T) {
	oversized := bytes.Repeat([]byte(" "), MaxBytes+1)
	if _, err := DecodeBytes(oversized); err == nil || !strings.Contains(err.Error(), "byte limit") {
		t.Fatalf("byte slice limit error = %v", err)
	}
	if _, err := Decode(bytes.NewReader(oversized)); err == nil || !strings.Contains(err.Error(), "byte limit") {
		t.Fatalf("reader limit error = %v", err)
	}
	deep := []byte(strings.Repeat("[", 66) + "0" + strings.Repeat("]", 66))
	if _, err := DecodeBytes(deep); err == nil || !strings.Contains(err.Error(), "nesting") {
		t.Fatalf("depth error = %v", err)
	}
	if _, err := Decode(errorReader{}); err == nil || !strings.Contains(err.Error(), "read input") {
		t.Fatalf("reader error = %v", err)
	}
	if _, err := Load("does-not-exist.json"); err == nil || !strings.Contains(err.Error(), "open") {
		t.Fatalf("open error = %v", err)
	}
}

func TestLexicalHelpersCoverCanonicalBoundaries(t *testing.T) {
	for _, value := range []string{"0", "1", "9223372036854775807", "-9223372036854775808"} {
		if _, err := parseInt64String(value, "test"); err != nil {
			t.Errorf("valid signed %q: %v", value, err)
		}
	}
	for _, value := range []any{"", "00", "-0", "+1", " 1", "1 ", "1.0", "1e0", "١", 1, nil} {
		if _, err := parseInt64String(value, "test"); err == nil {
			t.Errorf("invalid signed %#v accepted", value)
		}
	}
	if canonicalDecimal(strings.Repeat("9", 21), true) {
		t.Fatal("overlong decimal token accepted")
	}
	for _, value := range []string{"0", "1", "18446744073709551615"} {
		if _, err := parseUint64String(value, "test"); err != nil {
			t.Errorf("valid unsigned %q: %v", value, err)
		}
	}
	for _, value := range []any{"-1", "18446744073709551616", true} {
		if _, err := parseUint64String(value, "test"); err == nil {
			t.Errorf("invalid unsigned %#v accepted", value)
		}
	}
	if _, err := parseSmallInteger("1", "test"); err == nil {
		t.Fatal("string small integer accepted")
	}
}

type errorReader struct{}

func (errorReader) Read([]byte) (int, error) { return 0, errors.New("read failed") }

var _ io.Reader = errorReader{}
