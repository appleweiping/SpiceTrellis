package circuitir

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"unicode/utf8"
)

// Decode reads, strictly decodes and semantically validates a Circuit IR document.
func Decode(reader io.Reader) (Document, error) {
	limited := &io.LimitedReader{R: reader, N: MaxBytes + 1}
	data, err := io.ReadAll(limited)
	if err != nil {
		return Document{}, fmt.Errorf("circuit IR: read input: %w", err)
	}
	if len(data) > MaxBytes {
		return Document{}, fmt.Errorf("circuit IR: input exceeds the %d-byte limit", MaxBytes)
	}
	return DecodeBytes(data)
}

// DecodeBytes strictly decodes a complete UTF-8 document.
func DecodeBytes(data []byte) (Document, error) {
	if len(data) > MaxBytes {
		return Document{}, fmt.Errorf("circuit IR: input exceeds the %d-byte limit", MaxBytes)
	}
	if !utf8.Valid(data) {
		return Document{}, fmt.Errorf("circuit IR: input is not valid UTF-8")
	}
	if err := rejectDuplicateKeys(data); err != nil {
		return Document{}, fmt.Errorf("circuit IR: invalid JSON: %w", err)
	}
	generic, err := decodeGeneric(data)
	if err != nil {
		return Document{}, fmt.Errorf("circuit IR: invalid JSON: %w", err)
	}
	if err := validateShape(generic); err != nil {
		return Document{}, fmt.Errorf("circuit IR: %w", err)
	}

	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var document Document
	if err := decoder.Decode(&document); err != nil {
		return Document{}, fmt.Errorf("circuit IR: invalid field type: %w", err)
	}
	if err := requireEOF(decoder); err != nil {
		return Document{}, fmt.Errorf("circuit IR: invalid JSON: %w", err)
	}
	if err := document.Validate(); err != nil {
		return Document{}, err
	}
	return document, nil
}

// Load opens and decodes one Circuit IR file.
func Load(filename string) (Document, error) {
	file, err := os.Open(filename)
	if err != nil {
		return Document{}, fmt.Errorf("circuit IR: open %q: %w", filename, err)
	}
	defer file.Close()
	document, err := Decode(file)
	if err != nil {
		return Document{}, fmt.Errorf("circuit IR: decode %q: %w", filename, err)
	}
	return document, nil
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
	if depth > 512 {
		return fmt.Errorf("nesting exceeds 512 levels")
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
				return fmt.Errorf("duplicate object key %q at depth %d", diagnosticKey(key), depth)
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
		index := 0
		for decoder.More() {
			if err := scanValue(decoder, depth+1); err != nil {
				return err
			}
			index++
		}
		closing, err := decoder.Token()
		if err != nil {
			return err
		}
		if closing != json.Delim(']') {
			return fmt.Errorf("array at depth %d is not closed", depth)
		}
	default:
		return fmt.Errorf("unexpected delimiter %q at depth %d", delimiter, depth)
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

func decodeGeneric(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if err := requireEOF(decoder); err != nil {
		return nil, err
	}
	return value, nil
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

func validateShape(value any) error {
	root, err := exactObject(value, "$", []string{
		"schema", "schema_version", "entry", "modules", "models", "global_nodes",
		"dependencies", "losses",
	})
	if err != nil {
		return err
	}
	modules, err := shapeArray(root["modules"], "$.modules")
	if err != nil {
		return err
	}
	for index, value := range modules {
		location := fmt.Sprintf("$.modules[%d]", index)
		module, err := exactObject(value, location, []string{
			"name", "ports", "parameters", "instances", "source",
		})
		if err != nil {
			return err
		}
		if err := validateParameterShapes(module["parameters"], location+".parameters"); err != nil {
			return err
		}
		if module["source"] != nil {
			if err := validateSourceShape(module["source"], location+".source"); err != nil {
				return err
			}
		}
		instances, err := shapeArray(module["instances"], location+".instances")
		if err != nil {
			return err
		}
		for instanceIndex, value := range instances {
			instanceLocation := fmt.Sprintf("%s.instances[%d]", location, instanceIndex)
			instance, err := exactObject(value, instanceLocation, []string{
				"id", "name", "family", "connections", "model", "value", "source_form",
				"parameters", "source",
			})
			if err != nil {
				return err
			}
			if err := validateParameterShapes(
				instance["parameters"], instanceLocation+".parameters",
			); err != nil {
				return err
			}
			if err := validateSourceShape(instance["source"], instanceLocation+".source"); err != nil {
				return err
			}
		}
	}
	models, err := shapeArray(root["models"], "$.models")
	if err != nil {
		return err
	}
	for index, value := range models {
		location := fmt.Sprintf("$.models[%d]", index)
		model, err := exactObject(value, location, []string{
			"id", "module", "name", "kind", "source_form", "source",
		})
		if err != nil {
			return err
		}
		if err := validateSourceShape(model["source"], location+".source"); err != nil {
			return err
		}
	}
	losses, err := shapeArray(root["losses"], "$.losses")
	if err != nil {
		return err
	}
	for index, value := range losses {
		location := fmt.Sprintf("$.losses[%d]", index)
		loss, err := exactObject(value, location, []string{"module", "reason", "card", "source"})
		if err != nil {
			return err
		}
		if err := validateSourceShape(loss["source"], location+".source"); err != nil {
			return err
		}
	}
	return nil
}

func validateParameterShapes(value any, location string) error {
	parameters, err := shapeArray(value, location)
	if err != nil {
		return err
	}
	for index, value := range parameters {
		parameter, err := exactObject(
			value,
			fmt.Sprintf("%s[%d]", location, index),
			[]string{"name", "expression", "source"},
		)
		if err != nil {
			return err
		}
		if err := validateSourceShape(
			parameter["source"], fmt.Sprintf("%s[%d].source", location, index),
		); err != nil {
			return err
		}
	}
	return nil
}

func validateSourceShape(value any, location string) error {
	_, err := exactObject(value, location, []string{"file", "start", "end"})
	return err
}

func exactObject(value any, location string, keys []string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s must be an object", location)
	}
	expected := make(map[string]struct{}, len(keys))
	for _, key := range keys {
		expected[key] = struct{}{}
		if _, present := object[key]; !present {
			return nil, fmt.Errorf("%s is missing %q", location, key)
		}
	}
	for key := range object {
		if _, present := expected[key]; !present {
			return nil, fmt.Errorf("%s contains unknown field %q", location, key)
		}
	}
	return object, nil
}

func shapeArray(value any, location string) ([]any, error) {
	array, ok := value.([]any)
	if !ok {
		return nil, fmt.Errorf("%s must be an array", location)
	}
	return array, nil
}
