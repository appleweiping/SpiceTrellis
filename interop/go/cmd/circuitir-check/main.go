// circuitir-check validates Circuit IR v1 without a Python runtime.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/appleweiping/SpiceTrellis/interop/go/circuitir"
)

func run(arguments []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("circuitir-check", flag.ContinueOnError)
	flags.SetOutput(stderr)
	requireLossless := flags.Bool(
		"require-lossless", false, "return status 1 when the document declares a loss",
	)
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if flags.NArg() != 1 {
		fmt.Fprintln(stderr, "usage: circuitir-check [--require-lossless] FILE")
		return 2
	}
	document, err := circuitir.Load(flags.Arg(0))
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	summary, err := document.Summary()
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	encoder := json.NewEncoder(stdout)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(summary); err != nil {
		fmt.Fprintf(stderr, "circuit IR: write summary: %v\n", err)
		return 2
	}
	if *requireLossless && len(document.Losses) != 0 {
		return 1
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}
