// physicalir-check validates raw physical-library JSON v1 without Python.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/appleweiping/SpiceTrellis/interop/go/physicalir"
)

type summary struct {
	Schema      string `json:"schema"`
	Version     int    `json:"version"`
	Library     string `json:"library"`
	Libraries   int    `json:"libraries"`
	Layers      int    `json:"layers"`
	Cells       int    `json:"cells"`
	Shapes      int    `json:"shapes"`
	InputSHA256 string `json:"input_sha256"`
}

func librarySummary(library physicalir.Library, inputSHA256 string) summary {
	shapes := 0
	for _, cell := range library.Cells {
		if cell.Layout != nil {
			shapes += len(cell.Layout.Shapes)
		}
		if cell.Abstract != nil {
			shapes += len(cell.Abstract.Blockages)
			for _, port := range cell.Abstract.Ports {
				shapes += len(port.Shapes)
			}
		}
	}
	return summary{
		Schema:      library.Schema,
		Version:     library.Version,
		Library:     library.Name,
		Libraries:   1,
		Layers:      len(library.Technology.Layers),
		Cells:       len(library.Cells),
		Shapes:      shapes,
		InputSHA256: inputSHA256,
	}
}

func loadSummary(filename string) (summary, error) {
	file, err := os.Open(filename)
	if err != nil {
		return summary{}, fmt.Errorf("physical IR: open %q: %w", filename, err)
	}
	hash := sha256.New()
	library, decodeErr := physicalir.Decode(io.TeeReader(file, hash))
	closeErr := file.Close()
	if decodeErr != nil {
		return summary{}, fmt.Errorf("physical IR: decode %q: %w", filename, decodeErr)
	}
	if closeErr != nil {
		return summary{}, fmt.Errorf("physical IR: close %q: %w", filename, closeErr)
	}
	return librarySummary(library, hex.EncodeToString(hash.Sum(nil))), nil
}

func run(arguments []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("physicalir-check", flag.ContinueOnError)
	flags.SetOutput(stderr)
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if flags.NArg() != 1 {
		fmt.Fprintln(stderr, "usage: physicalir-check FILE")
		return 2
	}
	summary, err := loadSummary(flags.Arg(0))
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	encoder := json.NewEncoder(stdout)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(summary); err != nil {
		fmt.Fprintf(stderr, "physical IR: write summary: %v\n", err)
		return 2
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}
