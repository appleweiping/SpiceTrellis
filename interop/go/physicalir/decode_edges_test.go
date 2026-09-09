package physicalir

import (
	"encoding/json"
	"strings"
	"testing"
)

func validPointData() []any { return []any{"0", "0"} }

func validRectangleData() map[string]any {
	return map[string]any{"kind": "rectangle", "origin": validPointData(), "size": []any{"1", "1"}}
}

func validShapeData() map[string]any {
	return map[string]any{"layer": "wire", "net": nil, "geometry": validRectangleData()}
}

func validTransformData() map[string]any {
	return map[string]any{"origin": validPointData(), "clockwise": json.Number("0"), "reflect_x": false}
}

func validLayoutData() map[string]any {
	return map[string]any{"shapes": []any{}, "instances": []any{}, "annotations": []any{}}
}

func validAbstractData() map[string]any {
	return map[string]any{
		"outline": map[string]any{
			"kind": "polygon", "vertices": []any{[]any{"0", "0"}, []any{"1", "0"}, []any{"0", "1"}},
		},
		"ports": []any{}, "blockages": []any{},
	}
}

func requireFailure(t *testing.T, run func() error) {
	t.Helper()
	if err := run(); err == nil {
		t.Fatal("invalid input was accepted")
	}
}

func TestGeometryWireShapeFailuresAreTyped(t *testing.T) {
	tests := []any{
		nil,
		map[string]any{},
		map[string]any{"kind": "circle"},
		map[string]any{"kind": "rectangle", "origin": validPointData(), "size": []any{"1", "1"}, "extra": true},
		map[string]any{"kind": "rectangle", "origin": []any{"0"}, "size": []any{"1", "1"}},
		map[string]any{"kind": "rectangle", "origin": validPointData(), "size": nil},
		map[string]any{"kind": "rectangle", "origin": validPointData(), "size": []any{"1"}},
		map[string]any{"kind": "rectangle", "origin": validPointData(), "size": []any{true, "1"}},
		map[string]any{"kind": "rectangle", "origin": validPointData(), "size": []any{"1", true}},
		map[string]any{"kind": "polygon", "vertices": nil},
		map[string]any{"kind": "polygon", "vertices": []any{[]any{"0"}}},
		map[string]any{"kind": "path", "points": nil, "width": "1"},
		map[string]any{"kind": "path", "points": []any{[]any{"0"}}, "width": "1"},
		map[string]any{"kind": "path", "points": []any{validPointData(), []any{"1", "0"}}, "width": true},
	}
	for index, value := range tests {
		t.Run(string(rune('A'+index)), func(t *testing.T) {
			requireFailure(t, func() error {
				_, err := parseGeometry(value, "test", &resourceBudget{})
				return err
			})
		})
	}
	budget := &resourceBudget{polygonWork: MaxStoredPolygonWork}
	requireFailure(t, func() error {
		_, err := parseGeometry(
			map[string]any{"kind": "polygon", "vertices": []any{validPointData(), validPointData(), validPointData()}},
			"test", budget,
		)
		return err
	})
}

func TestShapeLayoutAndAbstractWireFailuresAreTyped(t *testing.T) {
	requireFailure(t, func() error {
		_, err := parseShape(nil, "test", &resourceBudget{})
		return err
	})
	for _, shape := range []map[string]any{
		{"layer": true, "net": nil, "geometry": validRectangleData()},
		{"layer": "wire", "net": false, "geometry": validRectangleData()},
		{"layer": "wire", "net": nil, "geometry": nil},
	} {
		requireFailure(t, func() error {
			_, err := parseShape(shape, "test", &resourceBudget{})
			return err
		})
	}
	requireFailure(t, func() error {
		_, err := parseShape(validShapeData(), "test", &resourceBudget{items: MaxItems})
		return err
	})
	requireFailure(t, func() error {
		_, err := parseShapes(nil, "test", &resourceBudget{})
		return err
	})

	layoutCases := []any{
		nil,
		map[string]any{"shapes": []any{}, "instances": nil, "annotations": []any{}},
		map[string]any{"shapes": []any{}, "instances": []any{}, "annotations": nil},
		map[string]any{"shapes": []any{}, "instances": []any{nil}, "annotations": []any{}},
		map[string]any{"shapes": []any{}, "instances": []any{map[string]any{"name": true, "cell": "c", "transform": validTransformData()}}, "annotations": []any{}},
		map[string]any{"shapes": []any{}, "instances": []any{map[string]any{"name": "x", "cell": true, "transform": validTransformData()}}, "annotations": []any{}},
		map[string]any{"shapes": []any{}, "instances": []any{map[string]any{"name": "x", "cell": "c", "transform": nil}}, "annotations": []any{}},
		map[string]any{"shapes": []any{}, "instances": []any{}, "annotations": []any{nil}},
		map[string]any{"shapes": []any{}, "instances": []any{}, "annotations": []any{map[string]any{"text": true, "at": validPointData()}}},
		map[string]any{"shapes": []any{}, "instances": []any{}, "annotations": []any{map[string]any{"text": "x", "at": nil}}},
		map[string]any{"shapes": nil, "instances": []any{}, "annotations": []any{}},
	}
	for _, value := range layoutCases {
		requireFailure(t, func() error {
			_, err := parseLayout(value, "test", &resourceBudget{})
			return err
		})
	}
	requireFailure(t, func() error {
		value := validLayoutData()
		value["annotations"] = []any{map[string]any{"text": "x", "at": validPointData()}}
		_, err := parseLayout(value, "test", &resourceBudget{points: MaxStoredPoints})
		return err
	})

	abstractCases := []any{
		nil,
		map[string]any{"outline": validRectangleData(), "ports": []any{}, "blockages": []any{}},
		map[string]any{"outline": validAbstractData()["outline"], "ports": nil, "blockages": []any{}},
		map[string]any{"outline": validAbstractData()["outline"], "ports": []any{nil}, "blockages": []any{}},
		map[string]any{"outline": validAbstractData()["outline"], "ports": []any{map[string]any{"name": true, "shapes": []any{}}}, "blockages": []any{}},
		map[string]any{"outline": validAbstractData()["outline"], "ports": []any{map[string]any{"name": "p", "shapes": nil}}, "blockages": []any{}},
		map[string]any{"outline": validAbstractData()["outline"], "ports": []any{}, "blockages": nil},
	}
	for _, value := range abstractCases {
		requireFailure(t, func() error {
			_, err := parseAbstract(value, "test", &resourceBudget{})
			return err
		})
	}
	requireFailure(t, func() error {
		_, err := parseAbstract(validAbstractData(), "test", &resourceBudget{polygonWork: MaxStoredPolygonWork})
		return err
	})
}

func TestCellTechnologyAndTopWireFailuresAreTyped(t *testing.T) {
	cellCases := []any{
		nil,
		map[string]any{"name": "c"},
		map[string]any{"name": true, "ports": []any{}, "layout": nil, "abstract": nil, "circuit_module": "m"},
		map[string]any{"name": "c", "ports": nil, "layout": nil, "abstract": nil, "circuit_module": "m"},
		map[string]any{"name": "c", "ports": []any{true}, "layout": nil, "abstract": nil, "circuit_module": "m"},
		map[string]any{"name": "c", "ports": []any{}, "layout": true, "abstract": nil, "circuit_module": nil},
		map[string]any{"name": "c", "ports": []any{}, "layout": nil, "abstract": true, "circuit_module": nil},
		map[string]any{"name": "c", "ports": []any{}, "layout": nil, "abstract": nil, "circuit_module": true},
	}
	for _, value := range cellCases {
		requireFailure(t, func() error {
			_, err := parseCell(value, "test", &resourceBudget{})
			return err
		})
	}
	requireFailure(t, func() error {
		value := map[string]any{
			"name": "c", "ports": []any{"p"}, "layout": validLayoutData(), "abstract": nil, "circuit_module": nil,
		}
		_, err := parseCell(value, "test", &resourceBudget{items: MaxItems})
		return err
	})

	technologyCases := []any{
		nil,
		map[string]any{"name": true, "layers": []any{}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{}, "packages": nil},
		map[string]any{"name": "t", "layers": []any{}, "packages": []any{true}},
		map[string]any{"name": "t", "layers": nil, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{nil}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{map[string]any{"id": true, "number": "0", "datatype": "0", "purpose": "unknown", "description": ""}}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{map[string]any{"id": "x", "number": true, "datatype": "0", "purpose": "unknown", "description": ""}}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{map[string]any{"id": "x", "number": "0", "datatype": true, "purpose": "unknown", "description": ""}}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{map[string]any{"id": "x", "number": "0", "datatype": "0", "purpose": true, "description": ""}}, "packages": []any{}},
		map[string]any{"name": "t", "layers": []any{map[string]any{"id": "x", "number": "0", "datatype": "0", "purpose": "unknown", "description": true}}, "packages": []any{}},
	}
	for _, value := range technologyCases {
		requireFailure(t, func() error {
			_, err := parseTechnology(value, "test")
			return err
		})
	}

	libraryCases := []any{
		nil,
		map[string]any{"schema": Schema},
		map[string]any{"schema": true, "version": json.Number("1"), "name": "x", "unit": "nm", "technology": map[string]any{}, "cells": []any{}},
		map[string]any{"schema": Schema, "version": "1", "name": "x", "unit": "nm", "technology": map[string]any{}, "cells": []any{}},
		map[string]any{"schema": Schema, "version": json.Number("1"), "name": true, "unit": "nm", "technology": map[string]any{}, "cells": []any{}},
		map[string]any{"schema": Schema, "version": json.Number("1"), "name": "x", "unit": true, "technology": map[string]any{}, "cells": []any{}},
		map[string]any{"schema": Schema, "version": json.Number("1"), "name": "x", "unit": "nm", "technology": nil, "cells": []any{}},
	}
	for _, value := range libraryCases {
		requireFailure(t, func() error {
			_, err := parseLibrary(value)
			return err
		})
	}
}

func TestScannerAndDiagnosticsHandleEscapesWithoutAmplification(t *testing.T) {
	for _, input := range [][]byte{
		[]byte(`{"name":"line\nquote\"slash\\unicode\u0041"}`),
		[]byte(`{"name":"\u00a9"}`),
		[]byte(`{"name":"\uD83D\uDE00"}`),
	} {
		if err := rejectLoneSurrogateEscapes(input); err != nil {
			t.Fatalf("valid escape rejected: %v", err)
		}
	}
	for _, input := range [][]byte{[]byte(`"\u12x4"`), []byte(`"\u123"`), []byte(`"\`)} {
		_ = rejectLoneSurrogateEscapes(input) // JSON decoder, not this focused scanner, rejects syntax.
	}
	longKey := strings.Repeat("é", 80)
	if summary := diagnosticKey(longKey); len(summary) >= len(longKey) || !strings.Contains(summary, "160 bytes") {
		t.Fatalf("unbounded or invalid diagnostic key: %q", summary)
	}
	if diagnosticKey("short") != "short" {
		t.Fatal("short diagnostic key changed")
	}
	if _, ok := hexadecimal4([]byte("0aF9"), 0); !ok {
		t.Fatal("mixed-case hexadecimal escape rejected")
	}
	if _, ok := hexadecimal4([]byte("zzzz"), 0); ok {
		t.Fatal("invalid hexadecimal escape accepted")
	}
	if _, ok := hexadecimal4([]byte("123"), 0); ok {
		t.Fatal("short hexadecimal escape accepted")
	}
}
