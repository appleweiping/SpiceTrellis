package circuitir

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"runtime"
	"strings"
	"testing"
)

const fixtureFingerprint = "9a37a5fd9b60ffdf5f2277eb5cb3048c7f2acd07a1f9e99cfd5cf314a5927c44"

func fixtureBytes(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile("../../fixtures/minimal-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func fixtureDocument(t *testing.T) Document {
	t.Helper()
	document, err := DecodeBytes(fixtureBytes(t))
	if err != nil {
		t.Fatal(err)
	}
	return document
}

func TestPythonFixtureHasTheSameGoIdentity(t *testing.T) {
	document := fixtureDocument(t)
	if document.InstanceCount() != 1 {
		t.Fatalf("instance count = %d", document.InstanceCount())
	}
	fingerprint, err := document.Fingerprint()
	if err != nil {
		t.Fatal(err)
	}
	if fingerprint != fixtureFingerprint {
		t.Fatalf("fingerprint = %s", fingerprint)
	}
	summary, err := document.Summary()
	if err != nil {
		t.Fatal(err)
	}
	if summary.Fingerprint != fixtureFingerprint || summary.Modules != 1 || summary.Losses != 0 {
		t.Fatalf("unexpected summary: %#v", summary)
	}
	source := document.Modules[0].Instances[0].Source
	if source.Start[0] != 1 || source.Start[1] != 1 || source.End[0] != 1 || source.End[1] != 11 {
		t.Fatalf("source range = %#v; want one-based half-open [1,1]-[1,11]", source)
	}
}

func TestExpressionTokenBudgetRejectsFlatInputBeforeRendering(t *testing.T) {
	value := strings.Repeat("1+", 2_000) + "1"
	if _, err := parseExpression(value); err == nil || !strings.Contains(err.Error(), "1024-token limit") {
		t.Fatalf("flat expression error = %v", err)
	}
}

func TestExpressionNodeBudgetRejectsWideAST(t *testing.T) {
	value := strings.Repeat("1+", 256) + "1"
	if _, err := parseExpression(value); err == nil || !strings.Contains(err.Error(), "512-node limit") {
		t.Fatalf("wide expression error = %v", err)
	}
}

func stringPointer(value string) *string { return &value }

func complexDocument() Document {
	source := Source{File: "minimal.sp", Start: []int{1, 1}, End: []int{1, 10}}
	return Document{
		Schema: Schema, SchemaVersion: SchemaVersion, Entry: "minimal.sp",
		Dependencies: []string{"minimal.sp"}, GlobalNodes: []string{"vdd!"},
		Models: []Model{{
			ID: "$top::model::nm", Module: "$top", Name: "NM", Kind: "NMOS",
			SourceForm: "level=1", Source: source,
		}},
		Modules: []Module{
			{
				Name: "$top", Parameters: []Parameter{{Name: "gain", Expression: "{1 + {2 * 3}}", Source: source}},
				Ports: []string{}, Instances: []Instance{
					{ID: "$top::v1", Name: "V1", Family: "V", Connections: []string{"vdd!", "0"}, SourceForm: stringPointer("DC 1"), Parameters: []Parameter{}, Source: source},
					{ID: "$top::x1", Name: "X1", Family: "X", Connections: []string{"in", "out"}, Model: stringPointer("cell"), Parameters: []Parameter{{Name: "p", Expression: "2", Source: source}}, Source: source},
				},
			},
			{
				Name: "cell", Ports: []string{"in", "out"}, Source: &source,
				Parameters: []Parameter{{Name: "p", Expression: "1", Source: source}},
				Instances: []Instance{
					{ID: "cell::c1", Name: "C1", Family: "C", Connections: []string{"out", "0"}, Value: stringPointer("1p"), Parameters: []Parameter{}, Source: source},
					{ID: "cell::m1", Name: "M1", Family: "M", Connections: []string{"out", "in", "0", "0"}, Model: stringPointer("NM"), Parameters: []Parameter{{Name: "W", Expression: "1u", Source: source}}, Source: source},
				},
			},
		},
		Losses: []Loss{{Module: "$top", Reason: "unsupported directive", Card: ".tran 1n 10n", Source: source}},
	}
}

func cloneDocument(t *testing.T, document Document) Document {
	t.Helper()
	data, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	var result Document
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestComplexDocumentExercisesEverySupportedFamilyAndScope(t *testing.T) {
	document := complexDocument()
	if err := document.Validate(); err != nil {
		t.Fatal(err)
	}
	canonical, err := document.MarshalCanonical()
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := Decode(bytes.NewReader(canonical))
	if err != nil {
		t.Fatal(err)
	}
	if decoded.InstanceCount() != 4 || len(decoded.Models) != 1 || len(decoded.Losses) != 1 {
		t.Fatalf("unexpected decoded document: %#v", decoded)
	}
}

func TestCanonicalWriterCoversEveryJSONScalarAndEscape(t *testing.T) {
	value := map[string]any{
		"b": true,
		"n": nil,
		"a": []any{json.Number("2"), "\b\f\n\r\t\"\\\x01�"},
	}
	var buffer bytes.Buffer
	if err := writeCanonical(&buffer, value); err != nil {
		t.Fatal(err)
	}
	want := "{\"a\":[2,\"\\b\\f\\n\\r\\t\\\"\\\\\\u0001�\"],\"b\":true,\"n\":null}"
	if buffer.String() != want {
		t.Fatalf("canonical JSON = %q; want %q", buffer.String(), want)
	}
	buffer.Reset()
	if err := writeCanonical(&buffer, 1); err == nil || !strings.Contains(err.Error(), "int") {
		t.Fatalf("unsupported scalar error = %v", err)
	}
}

func TestSemanticValidationRejectsEachAmbiguousBoundary(t *testing.T) {
	tests := []struct {
		name    string
		message string
		mutate  func(*Document)
	}{
		{"schema", "unsupported schema", func(value *Document) { value.Schema = "other" }},
		{"no top", "begin with", func(value *Document) { value.Modules = nil }},
		{"too many modules", "4096-item", func(value *Document) { value.Modules = make([]Module, MaxModules+1); value.Modules[0].Name = "$top" }},
		{"too many top items", "top-level collection", func(value *Document) { value.GlobalNodes = make([]string, MaxItems+1) }},
		{"duplicate module", "duplicates a module", func(value *Document) { value.Modules = append(value.Modules, value.Modules[1]) }},
		{"top source", "must be null", func(value *Document) {
			source := value.Modules[1].Instances[0].Source
			value.Modules[0].Source = &source
		}},
		{"subcircuit source", "must identify", func(value *Document) { value.Modules[1].Source = nil }},
		{"duplicate port", "duplicates a token", func(value *Document) { value.Modules[1].Ports = []string{"IN", "in"} }},
		{"too many module ports", "ports or parameters", func(value *Document) { value.Modules[1].Ports = make([]string, 4_097) }},
		{"duplicate module parameter", "duplicates a parameter", func(value *Document) {
			value.Modules[1].Parameters = append(value.Modules[1].Parameters, value.Modules[1].Parameters[0])
		}},
		{"duplicate instance", "duplicates an instance", func(value *Document) {
			value.Modules[1].Instances = append(value.Modules[1].Instances, value.Modules[1].Instances[0])
		}},
		{"bad instance id", "not canonical", func(value *Document) { value.Modules[1].Instances[0].ID = "wrong" }},
		{"duplicate instance parameter", "duplicates a parameter", func(value *Document) {
			value.Modules[1].Instances[1].Parameters = append(value.Modules[1].Instances[1].Parameters, value.Modules[1].Instances[1].Parameters[0])
		}},
		{"too many instance parameters", "4096-item", func(value *Document) {
			value.Modules[1].Instances[0].Parameters = make([]Parameter, 4_097)
		}},
		{"bad identifier", "portable SPICE identifier", func(value *Document) { value.Modules[1].Instances[0].Name = "1bad" }},
		{"empty identifier", "must not be empty", func(value *Document) { value.Modules[1].Instances[0].Name = "" }},
		{"NUL identifier", "must not contain NUL", func(value *Document) { value.Modules[1].Instances[0].Name = "C\x00bad" }},
		{"long identifier", "character limit", func(value *Document) { value.Modules[1].Instances[0].Name = "C" + strings.Repeat("x", MaxStringRunes) }},
		{"unsupported family", "not supported", func(value *Document) { value.Modules[1].Instances[0].Family = "Q" }},
		{"family prefix", "element family", func(value *Document) {
			value.Modules[1].Instances[0].Name, value.Modules[1].Instances[0].ID = "R1", "cell::r1"
		}},
		{"nonascii node", "printable ASCII", func(value *Document) { value.Modules[1].Instances[0].Connections[0] = "nét" }},
		{"empty node", "must not be empty", func(value *Document) { value.Modules[1].Instances[0].Connections[0] = "" }},
		{"too many connections", "4096-item", func(value *Document) { value.Modules[1].Instances[0].Connections = make([]string, 4_097) }},
		{"invalid parameter expression", "is invalid", func(value *Document) { value.Modules[0].Parameters[0].Expression = "1 +" }},
		{"noncanonical parameter expression", "not canonical", func(value *Document) { value.Modules[0].Parameters[0].Expression = "{1+2}" }},
		{"invalid RCL fields", "invalid C fields", func(value *Document) { value.Modules[1].Instances[0].SourceForm = stringPointer("1p") }},
		{"invalid voltage fields", "invalid V fields", func(value *Document) { value.Modules[0].Instances[0].SourceForm = nil }},
		{"invalid MOS fields", "invalid M fields", func(value *Document) { value.Modules[1].Instances[1].Connections = []string{"d", "g", "s"} }},
		{"unknown model", "unknown model", func(value *Document) { value.Modules[1].Instances[1].Model = stringPointer("missing") }},
		{"invalid X fields", "invalid X fields", func(value *Document) { value.Modules[0].Instances[1].Value = stringPointer("1") }},
		{"unknown subcircuit", "unknown subcircuit", func(value *Document) { value.Modules[0].Instances[1].Model = stringPointer("missing") }},
		{"X port mismatch", "port count", func(value *Document) { value.Modules[0].Instances[1].Connections = []string{"one"} }},
		{"model module", "unknown module", func(value *Document) { value.Models[0].Module = "missing" }},
		{"model id", "not canonical", func(value *Document) { value.Models[0].ID = "wrong" }},
		{"model kind", "portable SPICE identifier", func(value *Document) { value.Models[0].Kind = "1bad" }},
		{"model source form", "Unicode scalar", func(value *Document) { value.Models[0].SourceForm = "�" }},
		{"duplicate model", "duplicates a model ID", func(value *Document) { value.Models = append(value.Models, value.Models[0]) }},
		{"global duplicate", "duplicates a token", func(value *Document) { value.GlobalNodes = []string{"VDD!", "vdd!"} }},
		{"dependency path", "relative POSIX", func(value *Document) { value.Dependencies[0] = "../minimal.sp" }},
		{"dependency duplicate", "duplicates a dependency", func(value *Document) { value.Dependencies = append(value.Dependencies, value.Dependencies[0]) }},
		{"entry missing", "not present", func(value *Document) { value.Entry = "other.sp" }},
		{"source shape", "line and column", func(value *Document) { value.Modules[0].Instances[0].Source.Start = []int{1} }},
		{"source positive", "from 1 through", func(value *Document) { value.Modules[0].Instances[0].Source.Start[0] = 0 }},
		{"source upper bound", "from 1 through", func(value *Document) { value.Modules[0].Instances[0].Source.Start[0] = MaxSourceCoordinate + 1 }},
		{"source order", "precedes", func(value *Document) { value.Modules[0].Instances[0].Source.Start = []int{2, 1} }},
		{"source closure", "dependency closure", func(value *Document) { value.Modules[0].Instances[0].Source.File = "other.sp" }},
		{"module source closure", "dependency closure", func(value *Document) { value.Modules[1].Source.File = "other.sp" }},
		{"module parameter source closure", "dependency closure", func(value *Document) { value.Modules[0].Parameters[0].Source.File = "other.sp" }},
		{"instance parameter source closure", "dependency closure", func(value *Document) { value.Modules[0].Instances[1].Parameters[0].Source.File = "other.sp" }},
		{"model source closure", "dependency closure", func(value *Document) { value.Models[0].Source.File = "other.sp" }},
		{"loss module", "unknown module", func(value *Document) { value.Losses[0].Module = "missing" }},
		{"loss reason", "must not be empty", func(value *Document) { value.Losses[0].Reason = "" }},
		{"loss card NUL", "must not contain NUL", func(value *Document) { value.Losses[0].Card = "\x00" }},
		{"loss source closure", "dependency closure", func(value *Document) { value.Losses[0].Source.File = "other.sp" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			document := cloneDocument(t, complexDocument())
			test.mutate(&document)
			if err := document.Validate(); err == nil || !strings.Contains(err.Error(), test.message) {
				t.Fatalf("error = %v, want substring %q", err, test.message)
			}
		})
	}
}

func TestCanonicalEncodingIsCompactSortedAndUTF8(t *testing.T) {
	document := fixtureDocument(t)
	canonical, err := document.MarshalCanonical()
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(canonical, []byte("\n")) {
		t.Fatal("canonical encoding contains a newline")
	}
	if !bytes.HasPrefix(canonical, []byte(`{"dependencies":`)) {
		t.Fatalf("keys are not sorted: %s", canonical[:40])
	}

	var buffer bytes.Buffer
	writeJSONString(&buffer, "snow 雪 <>& \u2028\n")
	if got, want := buffer.String(), "\"snow 雪 <>&  \\n\""; got != want {
		t.Fatalf("JSON string = %q, want %q", got, want)
	}
}

func TestStrictDecoderRejectsAmbiguousOrInvalidDocuments(t *testing.T) {
	valid := string(fixtureBytes(t))
	tests := map[string]string{
		"duplicate key": strings.Replace(valid, `"entry": "minimal.sp",`,
			`"entry": "minimal.sp", "entry": "other.sp",`, 1),
		"unknown field": strings.Replace(valid, `"entry": "minimal.sp",`,
			`"entry": "minimal.sp", "extra": true,`, 1),
		"missing field": strings.Replace(valid, `"entry": "minimal.sp",`, "", 1),
		"wrong version type": strings.Replace(valid, `"schema_version": 1`,
			`"schema_version": true`, 1),
		"absolute path": strings.Replace(valid, `"entry": "minimal.sp"`,
			`"entry": "C:\\minimal.sp"`, 1),
		"noncanonical id": strings.Replace(valid, `"id": "$top::r1"`,
			`"id": "$top::other"`, 1),
		"unsupported family": strings.Replace(valid, `"family": "R"`,
			`"family": "Q"`, 1),
		"family name mismatch": strings.Replace(valid, `"name": "R1"`,
			`"name": "C1"`, 1),
		"noncanonical expression": strings.Replace(valid, `"value": "1k"`,
			`"value": "{1+2}"`, 1),
		"foreign source": strings.Replace(valid, `"file": "minimal.sp"`,
			`"file": "other.sp"`, 1),
		"trailing value": valid + `{}`,
	}
	for name, input := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeBytes([]byte(input)); err == nil {
				t.Fatal("invalid input was accepted")
			}
		})
	}
}

func TestSharedCrossLanguageRejectionCorpus(t *testing.T) {
	var corpus struct {
		Schema        string `json:"schema"`
		SchemaVersion int    `json:"schema_version"`
		Cases         []struct {
			Name string `json:"name"`
			Old  string `json:"old"`
			New  string `json:"new"`
		} `json:"cases"`
	}
	data, err := os.ReadFile("../../fixtures/rejection-corpus-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	if corpus.Schema != "org.spicetrellis.circuit-ir-rejection-corpus" ||
		corpus.SchemaVersion != 1 || len(corpus.Cases) < 10 {
		t.Fatalf("unexpected rejection corpus header: %#v", corpus)
	}
	valid := string(fixtureBytes(t))
	for _, test := range corpus.Cases {
		t.Run(test.Name, func(t *testing.T) {
			mutated := strings.Replace(valid, test.Old, test.New, 1)
			if mutated == valid {
				t.Fatal("corpus replacement source was not found")
			}
			if _, err := DecodeBytes([]byte(mutated)); err == nil {
				t.Fatal("shared rejection case was accepted")
			}
		})
	}
}

func TestDecoderEnforcesEncodingSizeDepthAndReaderErrors(t *testing.T) {
	if _, err := DecodeBytes([]byte{0xff}); err == nil || !strings.Contains(err.Error(), "UTF-8") {
		t.Fatalf("invalid UTF-8 error = %v", err)
	}
	if _, err := DecodeBytes(bytes.Repeat([]byte(" "), MaxBytes+1)); err == nil ||
		!strings.Contains(err.Error(), "byte limit") {
		t.Fatalf("oversize error = %v", err)
	}
	if _, err := Decode(bytes.NewReader(bytes.Repeat([]byte(" "), MaxBytes+1))); err == nil ||
		!strings.Contains(err.Error(), "byte limit") {
		t.Fatalf("stream oversize error = %v", err)
	}
	deep := strings.Repeat("[", 514) + "0" + strings.Repeat("]", 514)
	if _, err := DecodeBytes([]byte(deep)); err == nil || !strings.Contains(err.Error(), "nesting") {
		t.Fatalf("deep input error = %v", err)
	}
	reader := errorReader{}
	if _, err := Decode(reader); err == nil || !strings.Contains(err.Error(), "read input") {
		t.Fatalf("reader error = %v", err)
	}
}

func TestDuplicateKeyScannerDoesNotAmplifyDeepUserKeys(t *testing.T) {
	key := strings.Repeat("k", 2_040)
	input := []byte(strings.Repeat(`{"`+key+`":`, 512) + "0" + strings.Repeat("}", 512))
	decoder := json.NewDecoder(bytes.NewReader(input))
	decoder.UseNumber()
	runtime.GC()
	var before runtime.MemStats
	var after runtime.MemStats
	runtime.ReadMemStats(&before)
	if err := scanValue(decoder, 0); err != nil {
		t.Fatal(err)
	}
	runtime.ReadMemStats(&after)
	allocated := after.TotalAlloc - before.TotalAlloc
	if allocated > uint64(len(input))*48 {
		t.Fatalf("scanner allocated %d bytes for %d input bytes", allocated, len(input))
	}
	longKey := strings.Repeat("é", 80)
	summary := diagnosticKey(longKey)
	if len(summary) >= len(longKey) || !strings.Contains(summary, "160 bytes") {
		t.Fatalf("unbounded or invalid key summary: %q", summary)
	}
}

func TestLoadReportsOpenErrorsAndInMemoryNullArrays(t *testing.T) {
	if _, err := Load("../../fixtures/minimal-v1.json"); err != nil {
		t.Fatalf("load valid fixture: %v", err)
	}
	if _, err := Load("does-not-exist.json"); err == nil || !strings.Contains(err.Error(), "open") {
		t.Fatalf("open error = %v", err)
	}
	document := fixtureDocument(t)
	document.Models = nil
	if _, err := document.MarshalCanonical(); err == nil || !strings.Contains(err.Error(), "shape") {
		t.Fatalf("nil collection error = %v", err)
	}
}

func TestNestedShapeRequiresExactObjectsAndArrays(t *testing.T) {
	valid := string(fixtureBytes(t))
	tests := []string{
		strings.Replace(valid, `"parameters": []`, `"parameters": {}`, 1),
		strings.Replace(valid, `"source": {`, `"source": {"extra": true,`, 1),
		strings.Replace(valid, `"connections": [`, `"connections": {"bad":`, 1),
	}
	for _, input := range tests {
		if _, err := DecodeBytes([]byte(input)); err == nil {
			t.Fatalf("invalid nested shape accepted: %.80s", input)
		}
	}
}

func TestCanonicalHelpersRejectUnsupportedInMemoryValuesAndEscapeControls(t *testing.T) {
	var buffer bytes.Buffer
	if err := writeCanonical(&buffer, 1.5); err == nil {
		t.Fatal("unsupported canonical value accepted")
	}
	buffer.Reset()
	writeJSONString(&buffer, "\b\f\t\r\x01\"\\")
	if got, want := buffer.String(), `"\b\f\t\r\u0001\"\\"`; got != want {
		t.Fatalf("escaped = %q, want %q", got, want)
	}

	document := complexDocument()
	document.Models = nil
	if _, err := document.Fingerprint(); err == nil {
		t.Fatal("fingerprint accepted a non-canonical in-memory shape")
	}
	document = complexDocument()
	document.Schema = "bad"
	if _, err := document.Summary(); err == nil {
		t.Fatal("summary accepted an invalid document")
	}
	if _, err := decodeGeneric([]byte("{")); err == nil {
		t.Fatal("generic decoder accepted malformed JSON")
	}
	if _, err := decodeGeneric([]byte("{} {}")); err == nil {
		t.Fatal("generic decoder accepted multiple JSON values")
	}
}

func TestExpressionsMatchTheProducerCanonicalGrammar(t *testing.T) {
	valid := []string{
		"1k", "2.5meg", "1e10000", "Name", "-2", "{1 + {2 * right}}", "{2 ^ {3 ^ 2}}",
	}
	for _, expression := range valid {
		if err := validateCanonicalExpression(expression, "test"); err != nil {
			t.Errorf("valid %q: %v", expression, err)
		}
	}
	invalid := []string{
		"", "{1+2}", "(1)", "1 ", "{1 + }", "sqrt(2)", strings.Repeat("-", 514) + "1",
		"١", "１", "1\u00a0+ 2", "1K", "1e10001", "1e9999999999999999999",
		strings.Repeat("1", MaxNumberBytes+1),
	}
	for _, expression := range invalid {
		if err := validateCanonicalExpression(expression, "test"); err == nil {
			t.Errorf("invalid %q was accepted", expression)
		}
	}
}

func TestReferenceAndFamilyRulesAreIndependent(t *testing.T) {
	document := fixtureDocument(t)
	modelName := "NM"
	document.Models = []Model{{
		ID: "$top::model::nm", Module: "$top", Name: modelName, Kind: "NMOS",
		SourceForm: "level=1", Source: document.Modules[0].Instances[0].Source,
	}}
	instance := &document.Modules[0].Instances[0]
	instance.Name = "M1"
	instance.ID = "$top::m1"
	instance.Family = "M"
	instance.Connections = []string{"d", "g", "s", "b"}
	instance.Model = &modelName
	instance.Value = nil
	if err := document.Validate(); err != nil {
		t.Fatalf("valid MOS document: %v", err)
	}

	document.Models = nil
	if err := document.Validate(); err == nil || !strings.Contains(err.Error(), "unknown model") {
		t.Fatalf("missing model error = %v", err)
	}

	document = fixtureDocument(t)
	instance = &document.Modules[0].Instances[0]
	target := "missing"
	instance.Name, instance.ID, instance.Family = "X1", "$top::x1", "X"
	instance.Model, instance.Value = &target, nil
	if err := document.Validate(); err == nil || !strings.Contains(err.Error(), "subcircuit") {
		t.Fatalf("missing subcircuit error = %v", err)
	}
}

type errorReader struct{}

func (errorReader) Read([]byte) (int, error) { return 0, errors.New("read failed") }

var _ io.Reader = errorReader{}
