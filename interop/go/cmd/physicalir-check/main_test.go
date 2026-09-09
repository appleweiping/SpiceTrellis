package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/appleweiping/SpiceTrellis/interop/go/physicalir"
)

type errorWriter struct{}

func (errorWriter) Write([]byte) (int, error) { return 0, errors.New("write failed") }

func physicalFixture() string {
	return filepath.Join("..", "..", "..", "fixtures", "physical-v1", "rich-v1.json")
}

func TestRunReportsDecodedCountsAndExactInputHash(t *testing.T) {
	data, err := os.ReadFile(physicalFixture())
	if err != nil {
		t.Fatal(err)
	}
	wantHash := sha256.Sum256(data)
	var stdout, stderr bytes.Buffer
	if status := run([]string{physicalFixture()}, &stdout, &stderr); status != 0 {
		t.Fatalf("status=%d stderr=%s", status, stderr.String())
	}
	var got summary
	if err := json.Unmarshal(stdout.Bytes(), &got); err != nil {
		t.Fatalf("summary is not JSON: %v: %s", err, stdout.String())
	}
	want := summary{
		Schema:      physicalir.Schema,
		Version:     physicalir.SchemaVersion,
		Library:     "interop-library",
		Libraries:   1,
		Layers:      2,
		Cells:       2,
		Shapes:      5,
		InputSHA256: hex.EncodeToString(wantHash[:]),
	}
	if got != want {
		t.Fatalf("summary=%#v, want %#v", got, want)
	}
}

func TestRunRejectsUsageIOInvalidAndOversizedInputs(t *testing.T) {
	for name, arguments := range map[string][]string{
		"missing argument": nil,
		"extra argument":   {"one", "two"},
		"unknown flag":     {"--unknown"},
		"missing file":     {filepath.Join(t.TempDir(), "missing.json")},
	} {
		t.Run(name, func(t *testing.T) {
			var stdout, stderr bytes.Buffer
			if status := run(arguments, &stdout, &stderr); status != 2 {
				t.Fatalf("status=%d", status)
			}
			if stdout.Len() != 0 || stderr.Len() == 0 {
				t.Fatalf("stdout=%q stderr=%q", stdout.String(), stderr.String())
			}
		})
	}

	for name, data := range map[string][]byte{
		"invalid":   []byte(`{"schema":"wrong"}`),
		"oversized": bytes.Repeat([]byte(" "), physicalir.MaxBytes+1),
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), name+".json")
			if err := os.WriteFile(path, data, 0o600); err != nil {
				t.Fatal(err)
			}
			var stdout, stderr bytes.Buffer
			if status := run([]string{path}, &stdout, &stderr); status != 2 {
				t.Fatalf("status=%d", status)
			}
			if stdout.Len() != 0 || !strings.Contains(stderr.String(), "physical") {
				t.Fatalf("stdout=%q stderr=%q", stdout.String(), stderr.String())
			}
		})
	}
}

func TestRunReportsSummaryWriteFailure(t *testing.T) {
	var stderr bytes.Buffer
	if status := run([]string{physicalFixture()}, errorWriter{}, &stderr); status != 2 {
		t.Fatalf("status=%d", status)
	}
	if !strings.Contains(stderr.String(), "write summary") {
		t.Fatalf("stderr=%q", stderr.String())
	}
}
