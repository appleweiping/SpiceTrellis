package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type errorWriter struct{}

func (errorWriter) Write([]byte) (int, error) { return 0, errors.New("write failed") }

func TestRunReportsAValidatedSummary(t *testing.T) {
	fixture := filepath.Join("..", "..", "..", "fixtures", "minimal-v1.json")
	var stdout, stderr bytes.Buffer
	if status := run([]string{fixture}, &stdout, &stderr); status != 0 {
		t.Fatalf("status=%d stderr=%s", status, stderr.String())
	}
	if !strings.Contains(stdout.String(), `"fingerprint": "9a37a5fd`) {
		t.Fatalf("summary=%s", stdout.String())
	}
}

func TestRunUsesDistinctPolicyAndInputExitCodes(t *testing.T) {
	fixture := filepath.Join("..", "..", "..", "fixtures", "minimal-v1.json")
	data, err := os.ReadFile(fixture)
	if err != nil {
		t.Fatal(err)
	}
	lossy := strings.Replace(
		string(data),
		`"losses": []`,
		`"losses": [{"module":"$top","reason":"unsupported","card":".x","source":{"file":"minimal.sp","start":[1,1],"end":[1,2]}}]`,
		1,
	)
	path := filepath.Join(t.TempDir(), "lossy.json")
	if err := os.WriteFile(path, []byte(lossy), 0o600); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if status := run([]string{"--require-lossless", path}, &stdout, &stderr); status != 1 {
		t.Fatalf("policy status=%d stderr=%s", status, stderr.String())
	}
	if !strings.Contains(stdout.String(), `"losses": 1`) {
		t.Fatalf("summary=%s", stdout.String())
	}

	stdout.Reset()
	stderr.Reset()
	if status := run([]string{"missing.json"}, &stdout, &stderr); status != 2 {
		t.Fatalf("invalid input status=%d", status)
	}
	if stderr.Len() == 0 {
		t.Fatal("missing error message")
	}
	if status := run(nil, &stdout, &stderr); status != 2 {
		t.Fatalf("usage status=%d", status)
	}
	if status := run([]string{"--unknown"}, &stdout, &stderr); status != 2 {
		t.Fatalf("flag status=%d", status)
	}
}

func TestRunReportsSummaryWriteFailure(t *testing.T) {
	fixture := filepath.Join("..", "..", "..", "fixtures", "minimal-v1.json")
	var stderr bytes.Buffer
	if status := run([]string{fixture}, errorWriter{}, &stderr); status != 2 {
		t.Fatalf("write failure status=%d", status)
	}
	if !strings.Contains(stderr.String(), "write summary") {
		t.Fatalf("write failure stderr=%q", stderr.String())
	}
}
