package cassette

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestValidateFileAcceptsMinimalCassette(t *testing.T) {
	path := writeTempCassette(t, strings.Join([]string{
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_test","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:abc"}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"ok"}}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_test","status":"success"}`,
	}, "\n"))

	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if !report.Valid() {
		t.Fatalf("expected valid report, got issues: %#v", report.Issues)
	}
	if report.EventCount != 4 {
		t.Fatalf("expected 4 events, got %d", report.EventCount)
	}
}

func TestValidateFileReportsShapeErrors(t *testing.T) {
	path := writeTempCassette(t, strings.Join([]string{
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_1"}`,
		`{"schema_version":"0.1","event":"unknown"}`,
	}, "\n"))

	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if report.Valid() {
		t.Fatal("expected invalid report")
	}

	messages := issueMessages(report)
	assertContains(t, messages, "cassette must start with trace.start")
	assertContains(t, messages, "missing provider")
	assertContains(t, messages, "unknown event type")
	assertContains(t, messages, "cassette must end with trace.end")
}

func TestValidateFileReportsInvalidJSON(t *testing.T) {
	path := writeTempCassette(t, "{bad json}\n")

	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if report.Valid() {
		t.Fatal("expected invalid report")
	}
	assertContains(t, issueMessages(report), "invalid JSON event")
}

func TestValidateFileRejectsMalformedAlternativeFields(t *testing.T) {
	path := writeTempCassette(t, strings.Join([]string{
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_test","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output_hash":""}`,
		`{"schema_version":"0.1","event":"tool.response","span_id":"sp_2","error":""}`,
		`{"schema_version":"0.1","event":"retrieval.call","span_id":"sp_3","query":null}`,
		`{"schema_version":"0.1","event":"retrieval.response","span_id":"sp_4","documents":"not an array"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_test","status":"success"}`,
	}, "\n"))

	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if report.Valid() {
		t.Fatal("expected invalid report")
	}

	messages := issueMessages(report)
	assertContains(t, messages, "output_hash must be a non-empty string")
	assertContains(t, messages, "error must be a non-empty string")
	assertContains(t, messages, "query must be a non-empty string")
	assertContains(t, messages, "documents must be an array")
}

func TestValidateFileRejectsEventsAfterTraceEnd(t *testing.T) {
	path := writeTempCassette(t, strings.Join([]string{
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_test","name":"unit"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_test","status":"success"}`,
		`{"schema_version":"0.1","event":"agent.step","name":"late"}`,
	}, "\n"))

	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if report.Valid() {
		t.Fatal("expected invalid report")
	}
	assertContains(t, issueMessages(report), "events cannot appear after trace.end")
}

func writeTempCassette(t *testing.T, body string) string {
	t.Helper()

	path := filepath.Join(t.TempDir(), "run.replay.jsonl")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write temp cassette: %v", err)
	}
	return path
}

func issueMessages(report Report) string {
	var messages []string
	for _, issue := range report.Issues {
		messages = append(messages, issue.Message)
	}
	return strings.Join(messages, "\n")
}

func assertContains(t *testing.T, haystack string, needle string) {
	t.Helper()
	if !strings.Contains(haystack, needle) {
		t.Fatalf("expected %q to contain %q", haystack, needle)
	}
}
