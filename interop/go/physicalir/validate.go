package physicalir

import (
	"fmt"
	"unicode/utf8"
)

func validationError(location, format string, arguments ...any) error {
	return fmt.Errorf("physical library: %s: %s", location, fmt.Sprintf(format, arguments...))
}

func validateText(value, location string, allowEmpty bool) error {
	if !utf8.ValidString(value) {
		return validationError(location, "is not valid UTF-8")
	}
	if !allowEmpty && value == "" {
		return validationError(location, "must not be empty")
	}
	if utf8.RuneCountInString(value) > MaxTextRunes {
		return validationError(location, "exceeds the %d-character limit", MaxTextRunes)
	}
	for _, character := range value {
		if character < 32 || character >= 127 && character <= 159 ||
			character >= 0xD800 && character <= 0xDFFF {
			return validationError(location, "contains a control or surrogate")
		}
	}
	return nil
}

type resourceBudget struct {
	items       int
	points      int
	polygonWork int
	textBytes   int
}

func (budget *resourceBudget) add(location string, items, points, work int) error {
	budget.items += items
	budget.points += points
	budget.polygonWork += work
	if budget.items > MaxItems {
		return validationError(location, "aggregate stored items exceed %d", MaxItems)
	}
	if budget.points > MaxStoredPoints {
		return validationError(location, "aggregate stored points exceed %d", MaxStoredPoints)
	}
	if budget.polygonWork > MaxStoredPolygonWork {
		return validationError(location, "aggregate polygon work exceeds %d", MaxStoredPolygonWork)
	}
	return nil
}

func (budget *resourceBudget) text(value, location string, allowEmpty bool) error {
	if err := validateText(value, location, allowEmpty); err != nil {
		return err
	}
	budget.textBytes += len(value)
	if budget.textBytes > MaxLibraryTextBytes {
		return validationError(location, "aggregate UTF-8 text exceeds %d bytes", MaxLibraryTextBytes)
	}
	return nil
}

func validUnit(unit DistanceUnit) bool {
	return unit == Micrometer || unit == Nanometer || unit == Angstrom
}

func validPurpose(purpose LayerPurpose) bool {
	switch purpose {
	case PurposeUnknown, PurposeDrawing, PurposePin, PurposeLabel, PurposeObstruction, PurposeOutline:
		return true
	default:
		return false
	}
}

func uniqueStrings(values []string, location string) error {
	seen := make(map[string]struct{}, len(values))
	for index, value := range values {
		itemLocation := fmt.Sprintf("%s[%d]", location, index)
		if err := validateText(value, itemLocation, false); err != nil {
			return err
		}
		if _, exists := seen[value]; exists {
			return validationError(itemLocation, "duplicates %q", value)
		}
		seen[value] = struct{}{}
	}
	return nil
}

func (library Library) Validate() error {
	if library.Schema != Schema || library.Version != SchemaVersion {
		return validationError("$", "unsupported schema or version")
	}
	if !validUnit(library.Unit) {
		return validationError("$.unit", "unsupported distance unit %q", library.Unit)
	}
	if len(library.Technology.Layers) > MaxDefinitions ||
		len(library.Technology.Packages) > MaxDefinitions || len(library.Cells) > MaxDefinitions {
		return validationError("$", "technology or cell definitions exceed %d", MaxDefinitions)
	}

	budget := &resourceBudget{}
	if err := budget.text(library.Name, "$.name", false); err != nil {
		return err
	}
	if err := budget.text(library.Technology.Name, "$.technology.name", false); err != nil {
		return err
	}
	if err := uniqueStrings(library.Technology.Packages, "$.technology.packages"); err != nil {
		return err
	}
	for index, value := range library.Technology.Packages {
		if err := budget.text(value, fmt.Sprintf("$.technology.packages[%d]", index), false); err != nil {
			return err
		}
	}

	layers := make(map[string]struct{}, len(library.Technology.Layers))
	numericLayers := make(map[[2]uint64]struct{}, len(library.Technology.Layers))
	for index, layer := range library.Technology.Layers {
		location := fmt.Sprintf("$.technology.layers[%d]", index)
		if err := budget.text(layer.ID, location+".id", false); err != nil {
			return err
		}
		if _, exists := layers[layer.ID]; exists {
			return validationError(location+".id", "duplicates layer ID %q", layer.ID)
		}
		layers[layer.ID] = struct{}{}
		pair := [2]uint64{layer.Number, layer.Datatype}
		if _, exists := numericLayers[pair]; exists {
			return validationError(location, "duplicates numeric layer/datatype pair")
		}
		numericLayers[pair] = struct{}{}
		if !validPurpose(layer.Purpose) {
			return validationError(location+".purpose", "unsupported layer purpose %q", layer.Purpose)
		}
		if err := budget.text(layer.Description, location+".description", true); err != nil {
			return err
		}
	}

	cells := make(map[string]*Cell, len(library.Cells))
	for index := range library.Cells {
		cell := &library.Cells[index]
		location := fmt.Sprintf("$.cells[%d]", index)
		if err := budget.text(cell.Name, location+".name", false); err != nil {
			return err
		}
		if _, exists := cells[cell.Name]; exists {
			return validationError(location+".name", "duplicates cell name %q", cell.Name)
		}
		cells[cell.Name] = cell
	}

	for index := range library.Cells {
		cell := &library.Cells[index]
		location := fmt.Sprintf("$.cells[%d]", index)
		if err := validateCell(cell, location, layers, budget); err != nil {
			return err
		}
	}
	if err := validateHierarchy(library.Cells, cells); err != nil {
		return err
	}
	return nil
}

func validateCell(cell *Cell, location string, layers map[string]struct{}, budget *resourceBudget) error {
	if len(cell.Ports) > MaxItems {
		return validationError(location+".ports", "exceeds %d items", MaxItems)
	}
	if cell.Layout == nil && cell.Abstract == nil && cell.CircuitModule == nil {
		return validationError(location, "requires layout, abstract, or circuit_module")
	}
	if err := uniqueStrings(cell.Ports, location+".ports"); err != nil {
		return err
	}
	if err := budget.add(location+".ports", len(cell.Ports), 0, 0); err != nil {
		return err
	}
	for index, port := range cell.Ports {
		if err := budget.text(port, fmt.Sprintf("%s.ports[%d]", location, index), false); err != nil {
			return err
		}
	}
	if cell.CircuitModule != nil {
		if err := budget.text(*cell.CircuitModule, location+".circuit_module", false); err != nil {
			return err
		}
	}
	if cell.Layout != nil {
		if err := validateLayout(cell.Layout, location+".layout", layers, budget); err != nil {
			return err
		}
	}
	if cell.Abstract != nil {
		declared := make(map[string]struct{}, len(cell.Ports))
		for _, name := range cell.Ports {
			declared[name] = struct{}{}
		}
		if err := validateAbstract(cell.Abstract, location+".abstract", layers, declared, budget); err != nil {
			return err
		}
	}
	return nil
}

func validateLayout(layout *Layout, location string, layers map[string]struct{}, budget *resourceBudget) error {
	if len(layout.Shapes) > MaxItems || len(layout.Instances) > MaxItems ||
		len(layout.Annotations) > MaxItems {
		return validationError(location, "a layout collection exceeds %d items", MaxItems)
	}
	if err := budget.add(location, len(layout.Instances)+len(layout.Annotations), len(layout.Annotations), 0); err != nil {
		return err
	}
	instanceNames := make(map[string]struct{}, len(layout.Instances))
	for index, instance := range layout.Instances {
		itemLocation := fmt.Sprintf("%s.instances[%d]", location, index)
		if err := budget.text(instance.Name, itemLocation+".name", false); err != nil {
			return err
		}
		if _, exists := instanceNames[instance.Name]; exists {
			return validationError(itemLocation+".name", "duplicates an instance name")
		}
		instanceNames[instance.Name] = struct{}{}
		if err := budget.text(instance.Cell, itemLocation+".cell", false); err != nil {
			return err
		}
		if instance.Transform.Clockwise != 0 && instance.Transform.Clockwise != 90 &&
			instance.Transform.Clockwise != 180 && instance.Transform.Clockwise != 270 {
			return validationError(itemLocation+".transform.clockwise", "unsupported orientation")
		}
	}
	for index, annotation := range layout.Annotations {
		if err := budget.text(annotation.Text, fmt.Sprintf("%s.annotations[%d].text", location, index), false); err != nil {
			return err
		}
	}
	for index := range layout.Shapes {
		if err := validateShape(&layout.Shapes[index], fmt.Sprintf("%s.shapes[%d]", location, index), layers, budget); err != nil {
			return err
		}
	}
	return nil
}

func validateAbstract(view *Abstract, location string, layers map[string]struct{}, declared map[string]struct{}, budget *resourceBudget) error {
	if len(view.Ports) > MaxItems || len(view.Blockages) > MaxItems {
		return validationError(location, "an abstract collection exceeds %d items", MaxItems)
	}
	outlineGeometry := Geometry{Kind: PolygonKind, Polygon: &view.Outline}
	points, work, _ := geometryCost(outlineGeometry)
	if err := budget.add(location+".outline", 0, points, work); err != nil {
		return err
	}
	if err := validatePolygon(view.Outline, location+".outline"); err != nil {
		return err
	}
	if err := budget.add(location+".ports", len(view.Ports), 0, 0); err != nil {
		return err
	}
	portNames := make(map[string]struct{}, len(view.Ports))
	for index := range view.Ports {
		port := &view.Ports[index]
		portLocation := fmt.Sprintf("%s.ports[%d]", location, index)
		if err := budget.text(port.Name, portLocation+".name", false); err != nil {
			return err
		}
		if _, exists := portNames[port.Name]; exists {
			return validationError(portLocation+".name", "duplicates an abstract port")
		}
		portNames[port.Name] = struct{}{}
		if _, exists := declared[port.Name]; !exists {
			return validationError(portLocation+".name", "is not present in the cell interface")
		}
		if len(port.Shapes) == 0 || len(port.Shapes) > MaxItems {
			return validationError(portLocation+".shapes", "requires 1..%d shapes", MaxItems)
		}
		for shapeIndex := range port.Shapes {
			shape := &port.Shapes[shapeIndex]
			shapeLocation := fmt.Sprintf("%s.shapes[%d]", portLocation, shapeIndex)
			if shape.Net != nil && *shape.Net != port.Name {
				return validationError(shapeLocation+".net", "does not match its abstract port")
			}
			if err := validateShape(shape, shapeLocation, layers, budget); err != nil {
				return err
			}
		}
	}
	for index := range view.Blockages {
		if err := validateShape(&view.Blockages[index], fmt.Sprintf("%s.blockages[%d]", location, index), layers, budget); err != nil {
			return err
		}
	}
	return nil
}

func validateShape(shape *Shape, location string, layers map[string]struct{}, budget *resourceBudget) error {
	if err := budget.add(location, 1, 0, 0); err != nil {
		return err
	}
	if err := budget.text(shape.Layer, location+".layer", false); err != nil {
		return err
	}
	if _, exists := layers[shape.Layer]; !exists {
		return validationError(location+".layer", "references an undeclared technology layer")
	}
	if shape.Net != nil {
		if err := budget.text(*shape.Net, location+".net", false); err != nil {
			return err
		}
	}
	points, work, err := geometryCost(shape.Geometry)
	if err != nil {
		return validationError(location+".geometry", "%v", err)
	}
	if err := budget.add(location+".geometry", 0, points, work); err != nil {
		return err
	}
	return validateGeometry(shape.Geometry, location+".geometry")
}

func validateHierarchy(definitions []Cell, cells map[string]*Cell) error {
	for index, cell := range definitions {
		if cell.Layout == nil {
			continue
		}
		for instanceIndex, instance := range cell.Layout.Instances {
			if _, exists := cells[instance.Cell]; !exists {
				return validationError(
					fmt.Sprintf("$.cells[%d].layout.instances[%d].cell", index, instanceIndex),
					"references unknown cell %q", instance.Cell,
				)
			}
		}
	}
	state := make(map[string]uint8, len(cells))
	depth := make(map[string]int, len(cells))
	var visit func(string, int) (int, error)
	visit = func(name string, activeDepth int) (int, error) {
		if activeDepth > MaxDepth {
			return 0, validationError("$.cells", "physical hierarchy exceeds depth %d", MaxDepth)
		}
		if state[name] == 1 {
			return 0, validationError("$.cells", "physical hierarchy contains a cycle at %q", name)
		}
		if state[name] == 2 {
			return depth[name], nil
		}
		state[name] = 1
		maximumChildDepth := 0
		cell := cells[name]
		if cell.Layout != nil {
			for _, instance := range cell.Layout.Instances {
				childDepth, err := visit(instance.Cell, activeDepth+1)
				if err != nil {
					return 0, err
				}
				maximumChildDepth = max(maximumChildDepth, childDepth)
			}
		}
		state[name] = 2
		depth[name] = maximumChildDepth + 1
		if depth[name] > MaxDepth {
			return 0, validationError("$.cells", "physical hierarchy exceeds depth %d", MaxDepth)
		}
		return depth[name], nil
	}
	for name := range cells {
		if _, err := visit(name, 1); err != nil {
			return err
		}
	}
	return nil
}
