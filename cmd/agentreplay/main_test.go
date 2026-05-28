package main

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestRunRecordSetsEnvAndValidatesCassette(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based CLI wrapper test")
	}

	cassettePath := filepath.Join(t.TempDir(), "recorded.replay.jsonl")
	script := `test "$AGENTREPLAY_MODE" = record && ` +
		`test "$AGENTREPLAY_RECORD_OUT" = "$AGENTREPLAY_CASSETTE" && ` +
		`printf '%s\n' ` +
		`'{"schema_version":"0.1","event":"trace.start","trace_id":"tr_cli","name":"cli"}' ` +
		`'{"schema_version":"0.1","event":"llm.call","trace_id":"tr_cli","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:input","params":{"temperature":0}}' ` +
		`'{"schema_version":"0.1","event":"llm.response","trace_id":"tr_cli","span_id":"sp_1","output":{"text":"ok"},"output_hash":"sha256:output"}' ` +
		`'{"schema_version":"0.1","event":"trace.end","trace_id":"tr_cli","status":"success","output_hash":"sha256:output"}' ` +
		`> "$AGENTREPLAY_CASSETTE"`

	err := run([]string{"record", "--out", cassettePath, "--", "sh", "-c", script})
	if err != nil {
		t.Fatalf("run record returned error: %v", err)
	}
}

func TestRunReplaySetsEnvAndRunsCommand(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based CLI wrapper test")
	}

	cassettePath := writeCLITestCassette(t)
	script := `test "$AGENTREPLAY_MODE" = replay && ` +
		`test "$AGENTREPLAY_REPLAY_PATH" = "$AGENTREPLAY_CASSETTE" && ` +
		`test "$AGENTREPLAY_CASSETTE" = "` + cassettePath + `"`

	err := run([]string{"replay", cassettePath, "--", "sh", "-c", script})
	if err != nil {
		t.Fatalf("run replay returned error: %v", err)
	}
}

func TestRunReplayPropagatesChildExitCode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based CLI wrapper test")
	}

	cassettePath := writeCLITestCassette(t)
	err := run([]string{"replay", cassettePath, "--", "sh", "-c", "exit 7"})
	if err == nil {
		t.Fatal("expected child exit error")
	}

	var exitErr exitCodeError
	if !errors.As(err, &exitErr) {
		t.Fatalf("expected exitCodeError, got %T: %v", err, err)
	}
	if exitErr.ExitCode() != 7 {
		t.Fatalf("expected exit code 7, got %d", exitErr.ExitCode())
	}
}

func TestRunRecordRequiresSeparator(t *testing.T) {
	err := run([]string{"record", "--out", "run.replay.jsonl"})
	if err == nil {
		t.Fatal("expected usage error")
	}
}

func TestRunRecordRejectsStaleCassetteWhenChildWritesNothing(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-based CLI wrapper test")
	}

	cassettePath := writeCLITestCassette(t)
	err := run([]string{"record", "--out", cassettePath, "--", "sh", "-c", "true"})
	if err == nil {
		t.Fatal("expected invalid cassette error")
	}
	if !strings.Contains(err.Error(), "record command wrote invalid cassette") {
		t.Fatalf("expected invalid cassette error, got %v", err)
	}

	body, readErr := os.ReadFile(cassettePath)
	if readErr != nil {
		t.Fatalf("read cassette: %v", readErr)
	}
	if len(body) != 0 {
		t.Fatalf("expected stale cassette to be truncated, got %q", string(body))
	}
}

func TestRunGenerateTestsWritesPytestFile(t *testing.T) {
	cassettePath := writeCLITestCassette(t)
	outPath := filepath.Join(t.TempDir(), "tests", "test_agent_replays.py")

	var err error
	output := captureStdout(t, func() {
		err = run([]string{"generate-tests", cassettePath, "--framework", "pytest", "--out", outPath})
	})
	if err != nil {
		t.Fatalf("run generate-tests returned error: %v", err)
	}
	if !strings.Contains(output, "Generated pytest tests: "+outPath+" (1 cassette(s))") {
		t.Fatalf("expected success output to include output path and cassette count, got %q", output)
	}

	body, readErr := os.ReadFile(outPath)
	if readErr != nil {
		t.Fatalf("read generated pytest file: %v", readErr)
	}
	generated := string(body)
	if !strings.Contains(generated, "from agentreplay.pytest import replay_case") {
		t.Fatalf("expected generated file to import replay_case, got %q", generated)
	}
	if !strings.Contains(generated, "test_agent_replay_regression") {
		t.Fatalf("expected generated test function, got %q", generated)
	}
}

func TestRunGenerateTestsRequiresCassette(t *testing.T) {
	outPath := filepath.Join(t.TempDir(), "test_agent_replays.py")

	err := run([]string{"generate-tests", "--framework", "pytest", "--out", outPath})
	if err == nil {
		t.Fatal("expected usage error")
	}
	if !strings.Contains(err.Error(), "usage: agentreplay generate-tests") {
		t.Fatalf("expected usage error, got %v", err)
	}
}

func TestRunGenerateTestsRequiresFramework(t *testing.T) {
	cassettePath := writeCLITestCassette(t)
	outPath := filepath.Join(t.TempDir(), "test_agent_replays.py")

	err := run([]string{"generate-tests", cassettePath, "--out", outPath})
	if err == nil {
		t.Fatal("expected usage error")
	}
	if !strings.Contains(err.Error(), "usage: agentreplay generate-tests") {
		t.Fatalf("expected usage error, got %v", err)
	}
}

func TestRunGenerateTestsRejectsUnsupportedFramework(t *testing.T) {
	cassettePath := writeCLITestCassette(t)
	outPath := filepath.Join(t.TempDir(), "test_agent_replays.py")

	err := run([]string{"generate-tests", cassettePath, "--framework", "unittest", "--out", outPath})
	if err == nil {
		t.Fatal("expected unsupported framework error")
	}
	if !strings.Contains(err.Error(), "only pytest is supported") {
		t.Fatalf("expected unsupported framework error, got %v", err)
	}
}

func TestRunGenerateTestsRequiresOutputPath(t *testing.T) {
	cassettePath := writeCLITestCassette(t)

	err := run([]string{"generate-tests", cassettePath, "--framework", "pytest"})
	if err == nil {
		t.Fatal("expected usage error")
	}
	if !strings.Contains(err.Error(), "usage: agentreplay generate-tests") {
		t.Fatalf("expected usage error, got %v", err)
	}
}

func TestRunGenerateTestsRejectsInvalidCassetteWithoutOverwritingOutput(t *testing.T) {
	tempDir := t.TempDir()
	cassettePath := filepath.Join(tempDir, "invalid.replay.jsonl")
	outPath := filepath.Join(tempDir, "test_agent_replays.py")
	writeCLIFile(t, cassettePath, `{"schema_version":"0.1","event":"llm.call","span_id":"sp_1"}`+"\n")
	writeCLIFile(t, outPath, "keep me\n")

	err := run([]string{"generate-tests", cassettePath, "--framework", "pytest", "--out", outPath})
	if err == nil {
		t.Fatal("expected invalid cassette error")
	}
	if !strings.Contains(err.Error(), "is not a valid cassette") {
		t.Fatalf("expected invalid cassette error, got %v", err)
	}

	body, readErr := os.ReadFile(outPath)
	if readErr != nil {
		t.Fatalf("read output file: %v", readErr)
	}
	if string(body) != "keep me\n" {
		t.Fatalf("expected output file to remain unchanged, got %q", string(body))
	}
}

func TestRunGenerateTestsRejectsOutputCassetteCollision(t *testing.T) {
	cassettePath := writeCLITestCassette(t)
	before, readErr := os.ReadFile(cassettePath)
	if readErr != nil {
		t.Fatalf("read cassette before generate-tests: %v", readErr)
	}
	outPath := filepath.Join(filepath.Dir(cassettePath), ".", filepath.Base(cassettePath))

	err := run([]string{"generate-tests", cassettePath, "--framework", "pytest", "--out", outPath})
	if err == nil {
		t.Fatal("expected output collision error")
	}
	if !strings.Contains(err.Error(), "refusing to overwrite input cassette") {
		t.Fatalf("expected output collision error, got %v", err)
	}

	after, readErr := os.ReadFile(cassettePath)
	if readErr != nil {
		t.Fatalf("read cassette after generate-tests: %v", readErr)
	}
	if string(after) != string(before) {
		t.Fatalf("expected cassette to remain unchanged\nbefore: %q\nafter:  %q", string(before), string(after))
	}
}

func writeCLITestCassette(t *testing.T) string {
	t.Helper()

	cassettePath := filepath.Join(t.TempDir(), "run.replay.jsonl")
	script := `printf '%s\n' ` +
		`'{"schema_version":"0.1","event":"trace.start","trace_id":"tr_cli","name":"cli"}' ` +
		`'{"schema_version":"0.1","event":"llm.call","trace_id":"tr_cli","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:input","params":{"temperature":0}}' ` +
		`'{"schema_version":"0.1","event":"llm.response","trace_id":"tr_cli","span_id":"sp_1","output":{"text":"ok"},"output_hash":"sha256:output"}' ` +
		`'{"schema_version":"0.1","event":"trace.end","trace_id":"tr_cli","status":"success","output_hash":"sha256:output"}' ` +
		`> "` + cassettePath + `"`

	if err := run([]string{"record", "--out", cassettePath, "--", "sh", "-c", script}); err != nil {
		t.Fatalf("write cli test cassette: %v", err)
	}
	return cassettePath
}

func writeCLIFile(t *testing.T, path string, body string) {
	t.Helper()

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("create parent dir: %v", err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write file: %v", err)
	}
}

func captureStdout(t *testing.T, fn func()) string {
	t.Helper()

	originalStdout := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatalf("create stdout pipe: %v", err)
	}
	os.Stdout = writer

	fn()

	os.Stdout = originalStdout
	if err := writer.Close(); err != nil {
		t.Fatalf("close stdout writer: %v", err)
	}
	output, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read stdout: %v", err)
	}
	if err := reader.Close(); err != nil {
		t.Fatalf("close stdout reader: %v", err)
	}
	return string(output)
}
