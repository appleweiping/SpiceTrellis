package circuitir

import (
	"fmt"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"
)

var identifierPattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_.$:-]*$`)

var supportedFamilies = map[string]struct{}{
	"R": {}, "C": {}, "L": {}, "V": {}, "I": {}, "M": {}, "X": {},
}

// Validate checks all cross-field Circuit IR version 1 invariants.
func (document Document) Validate() error {
	if document.Schema != Schema || document.SchemaVersion != SchemaVersion {
		return validationError("$", "unsupported schema or version")
	}
	if err := validatePath(document.Entry, "$.entry"); err != nil {
		return err
	}
	if len(document.Modules) == 0 || document.Modules[0].Name != "$top" {
		return validationError("$.modules", "must begin with the $top module")
	}
	if len(document.Modules) > MaxModules {
		return validationError("$.modules", "exceeds the %d-item limit", MaxModules)
	}
	if len(document.Models) > MaxItems || len(document.GlobalNodes) > MaxItems ||
		len(document.Dependencies) > MaxItems || len(document.Losses) > MaxItems {
		return validationError("$", "a top-level collection exceeds the %d-item limit", MaxItems)
	}

	modules := make(map[string]Module, len(document.Modules))
	instanceIDs := make(map[string]struct{})
	instanceCount := 0
	for moduleIndex, module := range document.Modules {
		location := fmt.Sprintf("$.modules[%d]", moduleIndex)
		if err := validateModuleName(module.Name, location+".name"); err != nil {
			return err
		}
		moduleKey := fold(module.Name)
		if _, exists := modules[moduleKey]; exists {
			return validationError(location+".name", "duplicates a module name")
		}
		modules[moduleKey] = module
		if moduleIndex == 0 {
			if module.Source != nil {
				return validationError(location+".source", "must be null for $top")
			}
		} else {
			if module.Source == nil {
				return validationError(location+".source", "must identify the subcircuit definition")
			}
			if err := validateSource(*module.Source, location+".source"); err != nil {
				return err
			}
		}
		if len(module.Ports) > 4_096 || len(module.Parameters) > 4_096 {
			return validationError(location, "ports or parameters exceed the 4096-item limit")
		}
		if err := validateUniqueTokens(module.Ports, location+".ports"); err != nil {
			return err
		}
		if err := validateParameters(module.Parameters, location+".parameters"); err != nil {
			return err
		}
		instanceNames := make(map[string]struct{}, len(module.Instances))
		instanceCount += len(module.Instances)
		if instanceCount > MaxInstances {
			return validationError("$.modules", "exceeds the %d-instance limit", MaxInstances)
		}
		for instanceIndex, instance := range module.Instances {
			instanceLocation := fmt.Sprintf("%s.instances[%d]", location, instanceIndex)
			if err := validateIdentifier(instance.Name, instanceLocation+".name"); err != nil {
				return err
			}
			nameKey := fold(instance.Name)
			if _, exists := instanceNames[nameKey]; exists {
				return validationError(instanceLocation+".name", "duplicates an instance name")
			}
			instanceNames[nameKey] = struct{}{}
			expectedID := fold(module.Name) + "::" + fold(instance.Name)
			if instance.ID != expectedID {
				return validationError(instanceLocation+".id", "is not canonical")
			}
			if _, exists := instanceIDs[instance.ID]; exists {
				return validationError(instanceLocation+".id", "duplicates a global instance ID")
			}
			instanceIDs[instance.ID] = struct{}{}
			if err := validateInstanceBase(instance, instanceLocation); err != nil {
				return err
			}
		}
	}

	modelScopes := make(map[string]struct{}, len(document.Models))
	modelIDs := make(map[string]struct{}, len(document.Models))
	for index, model := range document.Models {
		location := fmt.Sprintf("$.models[%d]", index)
		if err := validateModuleName(model.Module, location+".module"); err != nil {
			return err
		}
		if _, exists := modules[fold(model.Module)]; !exists {
			return validationError(location+".module", "refers to an unknown module")
		}
		if err := validateIdentifier(model.Name, location+".name"); err != nil {
			return err
		}
		if err := validateIdentifier(model.Kind, location+".kind"); err != nil {
			return err
		}
		if err := validateString(model.SourceForm, location+".source_form", true); err != nil {
			return err
		}
		expectedID := fold(model.Module) + "::model::" + fold(model.Name)
		if model.ID != expectedID {
			return validationError(location+".id", "is not canonical")
		}
		if _, exists := modelIDs[model.ID]; exists {
			return validationError(location+".id", "duplicates a model ID")
		}
		modelIDs[model.ID] = struct{}{}
		modelScopes[fold(model.Module)+"\x00"+fold(model.Name)] = struct{}{}
		if err := validateSource(model.Source, location+".source"); err != nil {
			return err
		}
	}

	for moduleIndex, module := range document.Modules {
		for instanceIndex, instance := range module.Instances {
			location := fmt.Sprintf("$.modules[%d].instances[%d]", moduleIndex, instanceIndex)
			if err := validateInstanceReferences(instance, module, modules, modelScopes, location); err != nil {
				return err
			}
		}
	}

	if err := validateUniqueTokens(document.GlobalNodes, "$.global_nodes"); err != nil {
		return err
	}
	dependencies := make(map[string]struct{}, len(document.Dependencies))
	for index, dependency := range document.Dependencies {
		location := fmt.Sprintf("$.dependencies[%d]", index)
		if err := validatePath(dependency, location); err != nil {
			return err
		}
		key := strings.ToLower(dependency)
		if _, exists := dependencies[key]; exists {
			return validationError(location, "duplicates a dependency ignoring ASCII case")
		}
		dependencies[key] = struct{}{}
	}
	if _, exists := dependencies[strings.ToLower(document.Entry)]; !exists {
		return validationError("$.entry", "is not present in dependencies")
	}

	for moduleIndex, module := range document.Modules {
		if module.Source != nil {
			if err := validateSourceDependency(
				*module.Source, dependencies, fmt.Sprintf("$.modules[%d].source", moduleIndex),
			); err != nil {
				return err
			}
		}
		for parameterIndex, parameter := range module.Parameters {
			if err := validateSourceDependency(
				parameter.Source,
				dependencies,
				fmt.Sprintf("$.modules[%d].parameters[%d].source", moduleIndex, parameterIndex),
			); err != nil {
				return err
			}
		}
		for instanceIndex, instance := range module.Instances {
			location := fmt.Sprintf("$.modules[%d].instances[%d].source", moduleIndex, instanceIndex)
			if err := validateSourceDependency(instance.Source, dependencies, location); err != nil {
				return err
			}
			for parameterIndex, parameter := range instance.Parameters {
				if err := validateSourceDependency(
					parameter.Source,
					dependencies,
					fmt.Sprintf(
						"$.modules[%d].instances[%d].parameters[%d].source",
						moduleIndex,
						instanceIndex,
						parameterIndex,
					),
				); err != nil {
					return err
				}
			}
		}
	}
	for index, model := range document.Models {
		if err := validateSourceDependency(
			model.Source, dependencies, fmt.Sprintf("$.models[%d].source", index),
		); err != nil {
			return err
		}
	}
	for index, loss := range document.Losses {
		location := fmt.Sprintf("$.losses[%d]", index)
		if err := validateModuleName(loss.Module, location+".module"); err != nil {
			return err
		}
		if _, exists := modules[fold(loss.Module)]; !exists {
			return validationError(location+".module", "refers to an unknown module")
		}
		if err := validateString(loss.Reason, location+".reason", false); err != nil {
			return err
		}
		if err := validateString(loss.Card, location+".card", true); err != nil {
			return err
		}
		if err := validateSourceDependency(loss.Source, dependencies, location+".source"); err != nil {
			return err
		}
	}
	return nil
}

func validateModuleName(value, location string) error {
	if value == "$top" {
		return nil
	}
	return validateIdentifier(value, location)
}

func validateIdentifier(value, location string) error {
	if err := validateString(value, location, false); err != nil {
		return err
	}
	if !identifierPattern.MatchString(value) {
		return validationError(location, "must be a portable SPICE identifier")
	}
	return nil
}

func validateString(value, location string, allowEmpty bool) error {
	if !utf8.ValidString(value) {
		return validationError(location, "is not valid UTF-8")
	}
	if !allowEmpty && value == "" {
		return validationError(location, "must not be empty")
	}
	if utf8.RuneCountInString(value) > MaxStringRunes {
		return validationError(location, "exceeds the %d-character limit", MaxStringRunes)
	}
	if strings.ContainsRune(value, '\x00') {
		return validationError(location, "must not contain NUL")
	}
	if strings.ContainsRune(value, utf8.RuneError) {
		return validationError(location, "must contain only Unicode scalar values")
	}
	return nil
}

func validateToken(value, location string) error {
	if err := validateString(value, location, false); err != nil {
		return err
	}
	for _, character := range value {
		if character < 0x21 || character > 0x7e {
			return validationError(location, "must be a printable ASCII SPICE token")
		}
	}
	return nil
}

func validateUniqueTokens(values []string, location string) error {
	seen := make(map[string]struct{}, len(values))
	for index, value := range values {
		itemLocation := fmt.Sprintf("%s[%d]", location, index)
		if err := validateToken(value, itemLocation); err != nil {
			return err
		}
		key := fold(value)
		if _, exists := seen[key]; exists {
			return validationError(itemLocation, "duplicates a token ignoring case")
		}
		seen[key] = struct{}{}
	}
	return nil
}

func validateParameters(parameters []Parameter, location string) error {
	if len(parameters) > 4_096 {
		return validationError(location, "exceeds the 4096-item limit")
	}
	seen := make(map[string]struct{}, len(parameters))
	for index, parameter := range parameters {
		itemLocation := fmt.Sprintf("%s[%d]", location, index)
		if err := validateIdentifier(parameter.Name, itemLocation+".name"); err != nil {
			return err
		}
		key := fold(parameter.Name)
		if _, exists := seen[key]; exists {
			return validationError(itemLocation+".name", "duplicates a parameter")
		}
		seen[key] = struct{}{}
		if err := validateCanonicalExpression(parameter.Expression, itemLocation+".expression"); err != nil {
			return err
		}
		if err := validateSource(parameter.Source, itemLocation+".source"); err != nil {
			return err
		}
	}
	return nil
}

func validateInstanceBase(instance Instance, location string) error {
	if _, exists := supportedFamilies[instance.Family]; !exists {
		return validationError(location+".family", "is not supported by Circuit IR version 1")
	}
	if strings.ToUpper(instance.Name[:1]) != instance.Family {
		return validationError(location+".name", "does not match its element family")
	}
	if len(instance.Connections) > 4_096 {
		return validationError(location+".connections", "exceeds the 4096-item limit")
	}
	for index, connection := range instance.Connections {
		if err := validateToken(connection, fmt.Sprintf("%s.connections[%d]", location, index)); err != nil {
			return err
		}
	}
	if instance.Model != nil {
		if err := validateIdentifier(*instance.Model, location+".model"); err != nil {
			return err
		}
	}
	if instance.Value != nil {
		if err := validateCanonicalExpression(*instance.Value, location+".value"); err != nil {
			return err
		}
	}
	if instance.SourceForm != nil {
		if err := validateString(*instance.SourceForm, location+".source_form", true); err != nil {
			return err
		}
	}
	if err := validateParameters(instance.Parameters, location+".parameters"); err != nil {
		return err
	}
	return validateSource(instance.Source, location+".source")
}

func validateInstanceReferences(
	instance Instance,
	module Module,
	modules map[string]Module,
	modelScopes map[string]struct{},
	location string,
) error {
	switch instance.Family {
	case "R", "C", "L":
		if len(instance.Connections) != 2 || instance.Value == nil || instance.Model != nil ||
			instance.SourceForm != nil {
			return validationError(location, "has invalid %s fields", instance.Family)
		}
	case "V", "I":
		if len(instance.Connections) != 2 || instance.SourceForm == nil || *instance.SourceForm == "" ||
			instance.Value != nil || instance.Model != nil {
			return validationError(location, "has invalid %s fields", instance.Family)
		}
	case "M":
		if len(instance.Connections) != 4 || instance.Model == nil || instance.Value != nil ||
			instance.SourceForm != nil {
			return validationError(location, "has invalid M fields")
		}
		name := fold(*instance.Model)
		_, local := modelScopes[fold(module.Name)+"\x00"+name]
		_, global := modelScopes["$top\x00"+name]
		if !local && !global {
			return validationError(location+".model", "refers to an unknown model")
		}
	case "X":
		if instance.Model == nil || instance.Value != nil || instance.SourceForm != nil {
			return validationError(location, "has invalid X fields")
		}
		target, exists := modules[fold(*instance.Model)]
		if !exists {
			return validationError(location+".model", "refers to an unknown subcircuit")
		}
		if len(instance.Connections) != len(target.Ports) {
			return validationError(location+".connections", "does not match its subcircuit port count")
		}
	}
	return nil
}

func validateSource(source Source, location string) error {
	if err := validatePath(source.File, location+".file"); err != nil {
		return err
	}
	if len(source.Start) != 2 || len(source.End) != 2 {
		return validationError(location, "ranges must contain line and column")
	}
	for index, value := range append(append([]int{}, source.Start...), source.End...) {
		if value < 1 || value > MaxSourceCoordinate {
			return validationError(
				location, "range item %d must be from 1 through %d", index, MaxSourceCoordinate,
			)
		}
	}
	if source.End[0] < source.Start[0] ||
		(source.End[0] == source.Start[0] && source.End[1] < source.Start[1]) {
		return validationError(location, "end precedes start")
	}
	return nil
}

func validateSourceDependency(source Source, dependencies map[string]struct{}, location string) error {
	if err := validateSource(source, location); err != nil {
		return err
	}
	if _, exists := dependencies[strings.ToLower(source.File)]; !exists {
		return validationError(location+".file", "is outside the dependency closure")
	}
	return nil
}

func validatePath(value, location string) error {
	if err := validateString(value, location, false); err != nil {
		return err
	}
	if strings.ContainsAny(value, `\:`) || strings.HasPrefix(value, "/") || path.Clean(value) != value {
		return validationError(location, "must be a normalized relative POSIX path")
	}
	for _, character := range value {
		if character < 0x21 || character > 0x7e {
			return validationError(location, "must contain only printable ASCII path characters")
		}
	}
	for _, part := range strings.Split(value, "/") {
		if part == "" || part == "." || part == ".." {
			return validationError(location, "must be a normalized relative POSIX path")
		}
	}
	return nil
}

func fold(value string) string {
	// Identity-bearing names and tokens are ASCII by contract.
	return strings.ToLower(value)
}

func validationError(location, format string, arguments ...any) error {
	return fmt.Errorf("circuit IR: %s: %s", location, fmt.Sprintf(format, arguments...))
}
