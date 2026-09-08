// Package circuitir is a dependency-free Go consumer for SpiceTrellis Circuit IR v1.
//
// It is intentionally a consumer, not a second SPICE parser. Decode accepts an
// untrusted JSON document, rejects ambiguous JSON and validates the same stable
// hierarchy, identity, reference, expression and resource invariants as the
// Python producer.
package circuitir

const (
	Schema              = "org.spicetrellis.circuit-ir"
	SchemaVersion       = 1
	MaxBytes            = 4 * 1024 * 1024
	MaxModules          = 4_096
	MaxInstances        = 1_000_000
	MaxItems            = 100_000
	MaxStringRunes      = 1_000_000
	MaxSourceCoordinate = 2_147_483_647
	MaxExpressionNodes  = 512
	MaxExpressionTokens = 1_024
	MaxNumberBytes      = 128
)

// Source is a one-based, half-open source range in the dependency closure:
// Start is inclusive, End is exclusive, and columns count Unicode scalar values
// rather than UTF-8 bytes or grapheme clusters.
type Source struct {
	File  string `json:"file"`
	Start []int  `json:"start"`
	End   []int  `json:"end"`
}

// Parameter retains an identifier and its canonical portable expression.
type Parameter struct {
	Name       string `json:"name"`
	Expression string `json:"expression"`
	Source     Source `json:"source"`
}

// Model is a scoped SPICE model declaration.
type Model struct {
	ID         string `json:"id"`
	Module     string `json:"module"`
	Name       string `json:"name"`
	Kind       string `json:"kind"`
	SourceForm string `json:"source_form"`
	Source     Source `json:"source"`
}

// Instance is one supported primitive or hierarchical X instance.
type Instance struct {
	ID          string      `json:"id"`
	Name        string      `json:"name"`
	Family      string      `json:"family"`
	Connections []string    `json:"connections"`
	Model       *string     `json:"model"`
	Value       *string     `json:"value"`
	SourceForm  *string     `json:"source_form"`
	Parameters  []Parameter `json:"parameters"`
	Source      Source      `json:"source"`
}

// Loss preserves a card for which Circuit IR v1 has no structured mapping.
type Loss struct {
	Module string `json:"module"`
	Reason string `json:"reason"`
	Card   string `json:"card"`
	Source Source `json:"source"`
}

// Module is the explicit top-level or one subcircuit definition.
type Module struct {
	Name       string      `json:"name"`
	Ports      []string    `json:"ports"`
	Parameters []Parameter `json:"parameters"`
	Instances  []Instance  `json:"instances"`
	Source     *Source     `json:"source"`
}

// Document is the complete Circuit IR v1 wire document.
type Document struct {
	Schema        string   `json:"schema"`
	SchemaVersion int      `json:"schema_version"`
	Entry         string   `json:"entry"`
	Modules       []Module `json:"modules"`
	Models        []Model  `json:"models"`
	GlobalNodes   []string `json:"global_nodes"`
	Dependencies  []string `json:"dependencies"`
	Losses        []Loss   `json:"losses"`
}

// InstanceCount returns the number of instances across all modules.
func (document Document) InstanceCount() int {
	count := 0
	for _, module := range document.Modules {
		count += len(module.Instances)
	}
	return count
}

// Summary is the small stable report emitted by the Go checker command.
type Summary struct {
	Schema        string `json:"schema"`
	SchemaVersion int    `json:"schema_version"`
	Fingerprint   string `json:"fingerprint"`
	Modules       int    `json:"modules"`
	Instances     int    `json:"instances"`
	Models        int    `json:"models"`
	Losses        int    `json:"losses"`
}

// Summary validates the document and reports its deterministic identity.
func (document Document) Summary() (Summary, error) {
	fingerprint, err := document.Fingerprint()
	if err != nil {
		return Summary{}, err
	}
	return Summary{
		Schema:        document.Schema,
		SchemaVersion: document.SchemaVersion,
		Fingerprint:   fingerprint,
		Modules:       len(document.Modules),
		Instances:     document.InstanceCount(),
		Models:        len(document.Models),
		Losses:        len(document.Losses),
	}, nil
}
