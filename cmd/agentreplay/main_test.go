package main

import (
	"errors"
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
