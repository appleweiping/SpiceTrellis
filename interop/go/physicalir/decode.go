package physicalir

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strconv"
	"unicode/utf8"
)

// Decode reads at most MaxBytes+1, strictly decodes, and semantically validates.
func Decode(reader io.Reader) (Library, error) {
	limited := &io.LimitedReader{R: reader, N: MaxBytes + 1}
	data, err := io.ReadAll(limited)
	if err != nil {
		return Library{}, fmt.Errorf("physical library: read input: %w", err)
	}
	if len(data) > MaxBytes {
		return Library{}, fmt.Errorf("physical library: input exceeds the %d-byte limit", MaxBytes)
	}
	return DecodeBytes(data)
}

// DecodeBytes strictly decodes one complete UTF-8 physical-library v1 document.
func DecodeBytes(data []byte) (Library, error) {
	if len(data) > MaxBytes {
		return Library{}, fmt.Errorf("physical library: input exceeds the %d-byte limit", MaxBytes)
	}
	if !utf8.Valid(data) {
		return Library{}, fmt.Errorf("physical library: input is not valid UTF-8")
	}
	if err := rejectLoneSurrogateEscapes(data); err != nil {
		return Library{}, fmt.Errorf("physical library: invalid JSON: %w", err)
	}
	if err := rejectDuplicateKeys(data); err != nil {
		return Library{}, fmt.Errorf("physical library: invalid JSON: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var generic any
	if err := decoder.Decode(&generic); err != nil {
		return Library{}, fmt.Errorf("physical library: invalid JSON: %w", err)
	}
	if err := requireEOF(decoder); err != nil {
		return Library{}, fmt.Errorf("physical library: invalid JSON: %w", err)
	}
	library, err := parseLibrary(generic)
	if err != nil {
		return Library{}, err
	}
	if err := library.Validate(); err != nil {
		return Library{}, err
	}
	return library, nil
}

// Load opens and decodes one bounded physical-library file.
func Load(filename string) (Library, error) {
	file, err := os.Open(filename)
	if err != nil {
		return Library{}, fmt.Errorf("physical library: open %q: %w", filename, err)
	}
	defer file.Close()
	library, err := Decode(file)
	if err != nil {
		return Library{}, fmt.Errorf("physical library: decode %q: %w", filename, err)
	}
	return library, nil
}

func rejectLoneSurrogateEscapes(data []byte) error {
	inString := false
	for index := 0; index < len(data); index++ {
		switch {
		case !inString && data[index] == '"':
			inString = true
		case inString && data[index] == '"':
			inString = false
		case inString && data[index] == '\\':
			if index+1 >= len(data) {
				continue
			}
			if data[index+1] != 'u' {
				index++
				continue
			}
			value, ok := hexadecimal4(data, index+2)
			if !ok {
				continue
			}
			switch {
			case 0xD800 <= value && value <= 0xDBFF:
				if index+12 > len(data) || data[index+6] != '\\' || data[index+7] != 'u' {
					return fmt.Errorf("lone high-surrogate escape")
				}
				low, valid := hexadecimal4(data, index+8)
				if !valid || low < 0xDC00 || low > 0xDFFF {
					return fmt.Errorf("high-surrogate escape is not followed by a low surrogate")
				}
				index += 11
			case 0xDC00 <= value && value <= 0xDFFF:
				return fmt.Errorf("lone low-surrogate escape")
			default:
				index += 5
			}
		}
	}
	return nil
}

func hexadecimal4(data []byte, start int) (uint16, bool) {
	if start+4 > len(data) {
		return 0, false
	}
	var result uint16
	for _, character := range data[start : start+4] {
		result <<= 4
		switch {
		case '0' <= character && character <= '9':
			result += uint16(character - '0')
		case 'a' <= character && character <= 'f':
			result += uint16(character-'a') + 10
		case 'A' <= character && character <= 'F':
			result += uint16(character-'A') + 10
		default:
			return 0, false
		}
	}
	return result, true
}

func rejectDuplicateKeys(data []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := scanValue(decoder, 0); err != nil {
		return err
	}
	return requireEOF(decoder)
}

func scanValue(decoder *json.Decoder, depth int) error {
	if depth > 64 {
		return fmt.Errorf("nesting exceeds 64 levels")
	}
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return nil
	}
	switch delimiter {
	case '{':
		seen := make(map[string]struct{})
		for decoder.More() {
			rawKey, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := rawKey.(string)
			if !ok {
				return fmt.Errorf("object key at depth %d is not a string", depth)
			}
			if _, exists := seen[key]; exists {
				return fmt.Errorf("duplicate object key %q", diagnosticKey(key))
			}
			seen[key] = struct{}{}
			if err := scanValue(decoder, depth+1); err != nil {
				return err
			}
		}
		closing, err := decoder.Token()
		if err != nil {
			return err
		}
		if closing != json.Delim('}') {
			return fmt.Errorf("object at depth %d is not closed", depth)
		}
	case '[':
		for decoder.More() {
			if err := scanValue(decoder, depth+1); err != nil {
				return err
			}
		}
		closing, err := decoder.Token()
		if err != nil {
			return err
		}
		if closing != json.Delim(']') {
			return fmt.Errorf("array at depth %d is not closed", depth)
		}
	default:
		return fmt.Errorf("unexpected delimiter %q", delimiter)
	}
	return nil
}

func diagnosticKey(key string) string {
	const maximumBytes = 64
	if len(key) <= maximumBytes {
		return key
	}
	end := maximumBytes
	for end > 0 && !utf8.ValidString(key[:end]) {
		end--
	}
	return fmt.Sprintf("%s…[%d bytes]", key[:end], len(key))
}

func requireEOF(decoder *json.Decoder) error {
	if _, err := decoder.Token(); err != io.EOF {
		if err == nil {
			return fmt.Errorf("more than one JSON value")
		}
		return err
	}
	return nil
}

func exactObject(value any, location string, fields ...string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, validationError(location, "must be an object")
	}
	expected := make(map[string]struct{}, len(fields))
	for _, field := range fields {
		expected[field] = struct{}{}
		if _, present := object[field]; !present {
			return nil, validationError(location, "is missing field %q", field)
		}
	}
	for field := range object {
		if _, present := expected[field]; !present {
			return nil, validationError(location, "contains unknown field %q", diagnosticKey(field))
		}
	}
	return object, nil
}

func exactArray(value any, location string, maximum int) ([]any, error) {
	array, ok := value.([]any)
	if !ok {
		return nil, validationError(location, "must be an array")
	}
	if len(array) > maximum {
		return nil, validationError(location, "exceeds %d items", maximum)
	}
	return array, nil
}

func parseText(value any, location string, allowEmpty bool) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", validationError(location, "must be a string")
	}
	if err := validateText(text, location, allowEmpty); err != nil {
		return "", err
	}
	return text, nil
}

func parseOptionalText(value any, location string) (*string, error) {
	if value == nil {
		return nil, nil
	}
	text, err := parseText(value, location, false)
	if err != nil {
		return nil, err
	}
	return &text, nil
}

func canonicalDecimal(value string, signed bool) bool {
	// Every supported 64-bit value fits in at most 20 bytes, including the
	// minus sign on MinInt64.  Reject longer tokens before walking attacker-
	// controlled megabyte-scale strings.
	if len(value) > 20 {
		return false
	}
	if value == "0" {
		return true
	}
	start := 0
	if signed && len(value) > 0 && value[0] == '-' {
		start = 1
	}
	if start >= len(value) || value[start] < '1' || value[start] > '9' {
		return false
	}
	for index := start + 1; index < len(value); index++ {
		if value[index] < '0' || value[index] > '9' {
			return false
		}
	}
	return true
}

func parseInt64String(value any, location string) (int64, error) {
	text, ok := value.(string)
	if !ok || !canonicalDecimal(text, true) {
		return 0, validationError(location, "requires a canonical decimal string")
	}
	result, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		return 0, validationError(location, "is outside signed 64-bit range")
	}
	return result, nil
}

func parseUint64String(value any, location string) (uint64, error) {
	text, ok := value.(string)
	if !ok || !canonicalDecimal(text, false) {
		return 0, validationError(location, "requires a canonical unsigned decimal string")
	}
	result, err := strconv.ParseUint(text, 10, 64)
	if err != nil {
		return 0, validationError(location, "is outside unsigned 64-bit range")
	}
	return result, nil
}

func parseSmallInteger(value any, location string) (int, error) {
	number, ok := value.(json.Number)
	if !ok || !canonicalDecimal(number.String(), false) {
		return 0, validationError(location, "requires a canonical non-negative JSON integer")
	}
	result, err := strconv.Atoi(number.String())
	if err != nil {
		return 0, validationError(location, "integer is out of range")
	}
	return result, nil
}

func parsePoint(value any, location string) (Point, error) {
	array, err := exactArray(value, location, 2)
	if err != nil {
		return Point{}, err
	}
	if len(array) != 2 {
		return Point{}, validationError(location, "requires exactly two coordinates")
	}
	x, err := parseInt64String(array[0], location+"[0]")
	if err != nil {
		return Point{}, err
	}
	y, err := parseInt64String(array[1], location+"[1]")
	if err != nil {
		return Point{}, err
	}
	return Point{X: x, Y: y}, nil
}

func parseGeometry(value any, location string, budget *resourceBudget) (Geometry, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return Geometry{}, validationError(location, "must be an object")
	}
	kind, err := parseText(object["kind"], location+".kind", false)
	if err != nil {
		return Geometry{}, err
	}
	switch GeometryKind(kind) {
	case RectangleKind:
		object, err = exactObject(value, location, "kind", "origin", "size")
		if err != nil {
			return Geometry{}, err
		}
		origin, err := parsePoint(object["origin"], location+".origin")
		if err != nil {
			return Geometry{}, err
		}
		size, err := exactArray(object["size"], location+".size", 2)
		if err != nil || len(size) != 2 {
			if err != nil {
				return Geometry{}, err
			}
			return Geometry{}, validationError(location+".size", "requires exactly two dimensions")
		}
		width, err := parseInt64String(size[0], location+".size[0]")
		if err != nil {
			return Geometry{}, err
		}
		height, err := parseInt64String(size[1], location+".size[1]")
		if err != nil {
			return Geometry{}, err
		}
		if err := budget.add(location, 0, 4, 16); err != nil {
			return Geometry{}, err
		}
		rectangle := &Rectangle{Origin: origin, Width: width, Height: height}
		return Geometry{Kind: RectangleKind, Rectangle: rectangle}, nil
	case PolygonKind:
		object, err = exactObject(value, location, "kind", "vertices")
		if err != nil {
			return Geometry{}, err
		}
		values, err := exactArray(object["vertices"], location+".vertices", MaxPolygonVertices)
		if err != nil {
			return Geometry{}, err
		}
		if err := budget.add(location, 0, len(values), len(values)*len(values)); err != nil {
			return Geometry{}, err
		}
		vertices := make([]Point, len(values))
		for index, value := range values {
			vertices[index], err = parsePoint(value, fmt.Sprintf("%s.vertices[%d]", location, index))
			if err != nil {
				return Geometry{}, err
			}
		}
		polygon := &Polygon{Vertices: vertices}
		return Geometry{Kind: PolygonKind, Polygon: polygon}, nil
	case PathKind:
		object, err = exactObject(value, location, "kind", "points", "width")
		if err != nil {
			return Geometry{}, err
		}
		values, err := exactArray(object["points"], location+".points", MaxPathPoints)
		if err != nil {
			return Geometry{}, err
		}
		if err := budget.add(location, 0, len(values), 0); err != nil {
			return Geometry{}, err
		}
		points := make([]Point, len(values))
		for index, value := range values {
			points[index], err = parsePoint(value, fmt.Sprintf("%s.points[%d]", location, index))
			if err != nil {
				return Geometry{}, err
			}
		}
		width, err := parseInt64String(object["width"], location+".width")
		if err != nil {
			return Geometry{}, err
		}
		path := &Path{Points: points, Width: width}
		return Geometry{Kind: PathKind, Path: path}, nil
	default:
		return Geometry{}, validationError(location+".kind", "unknown geometry kind %q", kind)
	}
}

func parseShape(value any, location string, budget *resourceBudget) (Shape, error) {
	object, err := exactObject(value, location, "layer", "net", "geometry")
	if err != nil {
		return Shape{}, err
	}
	if err := budget.add(location, 1, 0, 0); err != nil {
		return Shape{}, err
	}
	layer, err := parseText(object["layer"], location+".layer", false)
	if err != nil {
		return Shape{}, err
	}
	net, err := parseOptionalText(object["net"], location+".net")
	if err != nil {
		return Shape{}, err
	}
	geometry, err := parseGeometry(object["geometry"], location+".geometry", budget)
	if err != nil {
		return Shape{}, err
	}
	return Shape{Layer: layer, Net: net, Geometry: geometry}, nil
}

func parseShapes(value any, location string, budget *resourceBudget) ([]Shape, error) {
	values, err := exactArray(value, location, MaxItems)
	if err != nil {
		return nil, err
	}
	shapes := make([]Shape, len(values))
	for index, value := range values {
		shapes[index], err = parseShape(value, fmt.Sprintf("%s[%d]", location, index), budget)
		if err != nil {
			return nil, err
		}
	}
	return shapes, nil
}

func parseTransform(value any, location string) (Transform, error) {
	object, err := exactObject(value, location, "origin", "clockwise", "reflect_x")
	if err != nil {
		return Transform{}, err
	}
	origin, err := parsePoint(object["origin"], location+".origin")
	if err != nil {
		return Transform{}, err
	}
	angle, err := parseSmallInteger(object["clockwise"], location+".clockwise")
	if err != nil {
		return Transform{}, err
	}
	reflect, ok := object["reflect_x"].(bool)
	if !ok {
		return Transform{}, validationError(location+".reflect_x", "must be a boolean")
	}
	return Transform{Origin: origin, Clockwise: angle, ReflectX: reflect}, nil
}

func parseLayout(value any, location string, budget *resourceBudget) (*Layout, error) {
	object, err := exactObject(value, location, "shapes", "instances", "annotations")
	if err != nil {
		return nil, err
	}
	instanceValues, err := exactArray(object["instances"], location+".instances", MaxItems)
	if err != nil {
		return nil, err
	}
	annotationValues, err := exactArray(object["annotations"], location+".annotations", MaxItems)
	if err != nil {
		return nil, err
	}
	if err := budget.add(location, len(instanceValues)+len(annotationValues), len(annotationValues), 0); err != nil {
		return nil, err
	}
	instances := make([]Instance, len(instanceValues))
	for index, value := range instanceValues {
		itemLocation := fmt.Sprintf("%s.instances[%d]", location, index)
		instance, err := exactObject(value, itemLocation, "name", "cell", "transform")
		if err != nil {
			return nil, err
		}
		name, err := parseText(instance["name"], itemLocation+".name", false)
		if err != nil {
			return nil, err
		}
		cell, err := parseText(instance["cell"], itemLocation+".cell", false)
		if err != nil {
			return nil, err
		}
		transform, err := parseTransform(instance["transform"], itemLocation+".transform")
		if err != nil {
			return nil, err
		}
		instances[index] = Instance{Name: name, Cell: cell, Transform: transform}
	}
	annotations := make([]Annotation, len(annotationValues))
	for index, value := range annotationValues {
		itemLocation := fmt.Sprintf("%s.annotations[%d]", location, index)
		annotation, err := exactObject(value, itemLocation, "text", "at")
		if err != nil {
			return nil, err
		}
		text, err := parseText(annotation["text"], itemLocation+".text", false)
		if err != nil {
			return nil, err
		}
		at, err := parsePoint(annotation["at"], itemLocation+".at")
		if err != nil {
			return nil, err
		}
		annotations[index] = Annotation{Text: text, At: at}
	}
	shapes, err := parseShapes(object["shapes"], location+".shapes", budget)
	if err != nil {
		return nil, err
	}
	return &Layout{Shapes: shapes, Instances: instances, Annotations: annotations}, nil
}

func parseAbstract(value any, location string, budget *resourceBudget) (*Abstract, error) {
	object, err := exactObject(value, location, "outline", "ports", "blockages")
	if err != nil {
		return nil, err
	}
	outline, err := parseGeometry(object["outline"], location+".outline", budget)
	if err != nil {
		return nil, err
	}
	if outline.Kind != PolygonKind || outline.Polygon == nil {
		return nil, validationError(location+".outline", "must be a polygon")
	}
	portValues, err := exactArray(object["ports"], location+".ports", MaxItems)
	if err != nil {
		return nil, err
	}
	if err := budget.add(location+".ports", len(portValues), 0, 0); err != nil {
		return nil, err
	}
	ports := make([]Port, len(portValues))
	for index, value := range portValues {
		portLocation := fmt.Sprintf("%s.ports[%d]", location, index)
		port, err := exactObject(value, portLocation, "name", "shapes")
		if err != nil {
			return nil, err
		}
		name, err := parseText(port["name"], portLocation+".name", false)
		if err != nil {
			return nil, err
		}
		shapes, err := parseShapes(port["shapes"], portLocation+".shapes", budget)
		if err != nil {
			return nil, err
		}
		ports[index] = Port{Name: name, Shapes: shapes}
	}
	blockages, err := parseShapes(object["blockages"], location+".blockages", budget)
	if err != nil {
		return nil, err
	}
	return &Abstract{Outline: *outline.Polygon, Ports: ports, Blockages: blockages}, nil
}

func parseCell(value any, location string, budget *resourceBudget) (Cell, error) {
	object, err := exactObject(value, location, "name", "ports", "layout", "abstract", "circuit_module")
	if err != nil {
		return Cell{}, err
	}
	name, err := parseText(object["name"], location+".name", false)
	if err != nil {
		return Cell{}, err
	}
	portValues, err := exactArray(object["ports"], location+".ports", MaxItems)
	if err != nil {
		return Cell{}, err
	}
	if err := budget.add(location+".ports", len(portValues), 0, 0); err != nil {
		return Cell{}, err
	}
	ports := make([]string, len(portValues))
	for index, value := range portValues {
		ports[index], err = parseText(value, fmt.Sprintf("%s.ports[%d]", location, index), false)
		if err != nil {
			return Cell{}, err
		}
	}
	var layout *Layout
	if object["layout"] != nil {
		layout, err = parseLayout(object["layout"], location+".layout", budget)
		if err != nil {
			return Cell{}, err
		}
	}
	var abstract *Abstract
	if object["abstract"] != nil {
		abstract, err = parseAbstract(object["abstract"], location+".abstract", budget)
		if err != nil {
			return Cell{}, err
		}
	}
	circuitModule, err := parseOptionalText(object["circuit_module"], location+".circuit_module")
	if err != nil {
		return Cell{}, err
	}
	return Cell{Name: name, Ports: ports, Layout: layout, Abstract: abstract, CircuitModule: circuitModule}, nil
}

func parseTechnology(value any, location string) (Technology, error) {
	object, err := exactObject(value, location, "name", "layers", "packages")
	if err != nil {
		return Technology{}, err
	}
	name, err := parseText(object["name"], location+".name", false)
	if err != nil {
		return Technology{}, err
	}
	packageValues, err := exactArray(object["packages"], location+".packages", MaxDefinitions)
	if err != nil {
		return Technology{}, err
	}
	packages := make([]string, len(packageValues))
	for index, value := range packageValues {
		packages[index], err = parseText(value, fmt.Sprintf("%s.packages[%d]", location, index), false)
		if err != nil {
			return Technology{}, err
		}
	}
	layerValues, err := exactArray(object["layers"], location+".layers", MaxDefinitions)
	if err != nil {
		return Technology{}, err
	}
	layers := make([]Layer, len(layerValues))
	for index, value := range layerValues {
		layerLocation := fmt.Sprintf("%s.layers[%d]", location, index)
		layer, err := exactObject(value, layerLocation, "id", "number", "datatype", "purpose", "description")
		if err != nil {
			return Technology{}, err
		}
		id, err := parseText(layer["id"], layerLocation+".id", false)
		if err != nil {
			return Technology{}, err
		}
		number, err := parseUint64String(layer["number"], layerLocation+".number")
		if err != nil {
			return Technology{}, err
		}
		datatype, err := parseUint64String(layer["datatype"], layerLocation+".datatype")
		if err != nil {
			return Technology{}, err
		}
		purpose, err := parseText(layer["purpose"], layerLocation+".purpose", false)
		if err != nil {
			return Technology{}, err
		}
		description, err := parseText(layer["description"], layerLocation+".description", true)
		if err != nil {
			return Technology{}, err
		}
		layers[index] = Layer{ID: id, Number: number, Datatype: datatype, Purpose: LayerPurpose(purpose), Description: description}
	}
	return Technology{Name: name, Layers: layers, Packages: packages}, nil
}

func parseLibrary(value any) (Library, error) {
	object, err := exactObject(value, "$", "schema", "version", "name", "unit", "technology", "cells")
	if err != nil {
		return Library{}, err
	}
	schema, err := parseText(object["schema"], "$.schema", false)
	if err != nil {
		return Library{}, err
	}
	version, err := parseSmallInteger(object["version"], "$.version")
	if err != nil {
		return Library{}, err
	}
	name, err := parseText(object["name"], "$.name", false)
	if err != nil {
		return Library{}, err
	}
	unit, err := parseText(object["unit"], "$.unit", false)
	if err != nil {
		return Library{}, err
	}
	technology, err := parseTechnology(object["technology"], "$.technology")
	if err != nil {
		return Library{}, err
	}
	cellValues, err := exactArray(object["cells"], "$.cells", MaxDefinitions)
	if err != nil {
		return Library{}, err
	}
	budget := &resourceBudget{}
	cells := make([]Cell, len(cellValues))
	for index, value := range cellValues {
		cells[index], err = parseCell(value, fmt.Sprintf("$.cells[%d]", index), budget)
		if err != nil {
			return Library{}, err
		}
	}
	return Library{Schema: schema, Version: version, Name: name, Unit: DistanceUnit(unit), Technology: technology, Cells: cells}, nil
}
