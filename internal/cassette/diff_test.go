package cassette

import (
	"strings"
	"testing"
)

func TestDiffEventsReportsNoDifferencesForIdenticalCassette(t *testing.T) {
	events := readValidCassetteEvents(t, minimalDiffCassette("gpt-4.1-mini", "sha256:input", "sha256:output", "same", `{"temperature":0}`)...)

	report, err := DiffEvents(events, events)
	if err != nil {
		t.Fatalf("DiffEvents returned error: %v", err)
	}
	if !report.Empty() {
		t.Fatalf("expected empty diff, got %#v", report.Differences)
	}
}

func TestDiffEventsReportsLLMCallAndResponseChanges(t *testing.T) {
	before := readValidCassetteEvents(t, minimalDiffCassette("gpt-4.1-mini", "sha256:input", "sha256:output", "before", `{"temperature":0}`)...)
	after := readValidCassetteEvents(t, minimalDiffCassette("gpt-4.1", "sha256:changed-input", "sha256:changed-output", "after", `{"temperature":1}`)...)

	report, err := DiffEvents(before, after)
	if err != nil {
		t.Fatalf("DiffEvents returned error: %v", err)
	}
	if report.Empty() {
		t.Fatal("expected differences")
	}

	messages := diffMessages(report)
	assertContains(t, messages, "llm exchange 1 call model")
	assertContains(t, messages, `"gpt-4.1-mini" -> "gpt-4.1"`)
	assertContains(t, messages, "llm exchange 1 call input_hash")
	assertContains(t, messages, "llm exchange 1 call params")
	assertContains(t, messages, "llm exchange 1 response output_hash")
	assertContains(t, messages, "llm exchange 1 response output")
}

func TestDiffEventsReportsAddedLLMExchange(t *testing.T) {
	before := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff","span_id":"sp_1","output":{"text":"first"},"output_hash":"sha256:first-output"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff","status":"success"}`,
	)
	after := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff_after","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_after","span_id":"sp_a","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_after","span_id":"sp_a","output":{"text":"first"},"output_hash":"sha256:first-output"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_after","span_id":"sp_b","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:second","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_after","span_id":"sp_b","output":{"text":"second"},"output_hash":"sha256:second-output"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff_after","status":"success"}`,
	)

	report, err := DiffEvents(before, after)
	if err != nil {
		t.Fatalf("DiffEvents returned error: %v", err)
	}

	messages := diffMessages(report)
	assertContains(t, messages, "llm exchange 2 exchange")
	assertContains(t, messages, "<missing> -> provider=openai model=gpt-4.1-mini input_hash=sha256:second response=sha256:second-output")
}

func TestDiffEventsReportsLLMErrorChanges(t *testing.T) {
	before := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff_before","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_before","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:input","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_before","span_id":"sp_1","error":"RateLimitError: before"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff_before","status":"error"}`,
	)
	after := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff_after","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_after","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:input","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_after","span_id":"sp_1","error":"RateLimitError: after"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff_after","status":"error"}`,
	)

	report, err := DiffEvents(before, after)
	if err != nil {
		t.Fatalf("DiffEvents returned error: %v", err)
	}

	messages := diffMessages(report)
	assertContains(t, messages, "llm exchange 1 response error")
	assertContains(t, messages, `"RateLimitError: before" -> "RateLimitError: after"`)
}

func TestDiffEventsReportsRemovedLLMExchange(t *testing.T) {
	before := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff_before","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_before","span_id":"sp_a","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_before","span_id":"sp_a","output":{"text":"first"},"output_hash":"sha256:first-output"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_before","span_id":"sp_b","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:removed","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_before","span_id":"sp_b","output":{"text":"removed"},"output_hash":"sha256:removed-output"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff_before","status":"success"}`,
	)
	after := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff_after","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff_after","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff_after","span_id":"sp_1","output":{"text":"first"},"output_hash":"sha256:first-output"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff_after","status":"success"}`,
	)

	report, err := DiffEvents(before, after)
	if err != nil {
		t.Fatalf("DiffEvents returned error: %v", err)
	}

	messages := diffMessages(report)
	assertContains(t, messages, "llm exchange 2 exchange")
	assertContains(t, messages, "provider=openai model=gpt-4.1-mini input_hash=sha256:removed response=sha256:removed-output -> <missing>")
}

func TestDiffFilesRejectsInvalidCassette(t *testing.T) {
	beforePath := writeTempCassette(t, `{"schema_version":"0.1","event":"llm.call","span_id":"sp_1"}`+"\n")
	afterPath := writeTempCassette(t, strings.Join(minimalDiffCassette("gpt-4.1-mini", "sha256:input", "sha256:output", "same", `{"temperature":0}`), "\n"))

	_, err := DiffFiles(beforePath, afterPath)
	if err == nil {
		t.Fatal("expected invalid cassette error")
	}
	assertContains(t, err.Error(), "is not a valid cassette")
	assertContains(t, err.Error(), "line 1")
}

func minimalDiffCassette(model string, inputHash string, outputHash string, output string, params string) []string {
	return []string{
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_diff","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","trace_id":"tr_diff","span_id":"sp_1","provider":"openai","model":"` + model + `","input_hash":"` + inputHash + `","params":` + params + `}`,
		`{"schema_version":"0.1","event":"llm.response","trace_id":"tr_diff","span_id":"sp_1","output":{"text":"` + output + `"},"output_hash":"` + outputHash + `"}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_diff","status":"success","output_hash":"` + outputHash + `"}`,
	}
}

func diffMessages(report DiffReport) string {
	var messages []string
	for _, difference := range report.Differences {
		messages = append(messages, difference.Location+" "+difference.Field+": "+difference.Before+" -> "+difference.After)
	}
	return strings.Join(messages, "\n")
}
