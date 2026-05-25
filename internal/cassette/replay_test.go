package cassette

import (
	"strings"
	"testing"
)

func TestReplayIndexBuildsOrderedLLMExchanges(t *testing.T) {
	events := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_replay","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_2","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:second","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_2","output":{"text":"second"}}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"first"}}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_replay","status":"success"}`,
	)

	index, err := NewReplayIndex(events)
	if err != nil {
		t.Fatalf("NewReplayIndex returned error: %v", err)
	}

	exchanges := index.LLMExchanges()
	if len(exchanges) != 2 {
		t.Fatalf("expected 2 exchanges, got %d", len(exchanges))
	}
	if exchanges[0].Call.SpanID != "sp_1" || exchanges[0].Response.SpanID != "sp_1" {
		t.Fatalf("expected first exchange to be sp_1, got call=%q response=%q", exchanges[0].Call.SpanID, exchanges[0].Response.SpanID)
	}
	if exchanges[1].Call.SpanID != "sp_2" || exchanges[1].Response.SpanID != "sp_2" {
		t.Fatalf("expected second exchange to be sp_2, got call=%q response=%q", exchanges[1].Call.SpanID, exchanges[1].Response.SpanID)
	}
}

func TestReplayIndexMatchLLMConsumesAndReturnsRecordedResponse(t *testing.T) {
	index := newTestReplayIndex(t)

	first, err := index.MatchLLM(LLMRequest{
		Provider:  "openai",
		Model:     "gpt-4.1-mini",
		InputHash: "sha256:first",
		Params: map[string]any{
			"temperature": 0,
		},
	})
	if err != nil {
		t.Fatalf("MatchLLM returned error: %v", err)
	}
	assertResponseText(t, first, "first response")

	second, err := index.MatchLLM(LLMRequest{
		Provider:  "openai",
		Model:     "gpt-4.1-mini",
		InputHash: "sha256:second",
		Params: map[string]any{
			"temperature": 0,
		},
	})
	if err != nil {
		t.Fatalf("MatchLLM returned error: %v", err)
	}
	assertResponseText(t, second, "second response")
}

func TestReplayIndexRejectsLLMMismatches(t *testing.T) {
	tests := []struct {
		name    string
		request LLMRequest
		errMsg  string
	}{
		{
			name: "provider mismatch",
			request: LLMRequest{
				Provider:  "anthropic",
				Model:     "gpt-4.1-mini",
				InputHash: "sha256:first",
				Params:    map[string]any{"temperature": 0},
			},
			errMsg: "provider mismatch",
		},
		{
			name: "model mismatch",
			request: LLMRequest{
				Provider:  "openai",
				Model:     "gpt-4.1",
				InputHash: "sha256:first",
				Params:    map[string]any{"temperature": 0},
			},
			errMsg: "model mismatch",
		},
		{
			name: "input hash mismatch",
			request: LLMRequest{
				Provider:  "openai",
				Model:     "gpt-4.1-mini",
				InputHash: "sha256:changed",
				Params:    map[string]any{"temperature": 0},
			},
			errMsg: "input_hash mismatch",
		},
		{
			name: "params mismatch",
			request: LLMRequest{
				Provider:  "openai",
				Model:     "gpt-4.1-mini",
				InputHash: "sha256:first",
				Params:    map[string]any{"temperature": 1},
			},
			errMsg: "params mismatch",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			index := newTestReplayIndex(t)
			_, err := index.MatchLLM(tt.request)
			if err == nil {
				t.Fatal("expected mismatch error")
			}
			if !strings.Contains(err.Error(), tt.errMsg) {
				t.Fatalf("expected error containing %q, got %q", tt.errMsg, err.Error())
			}

			response, err := index.MatchLLM(LLMRequest{
				Provider:  "openai",
				Model:     "gpt-4.1-mini",
				InputHash: "sha256:first",
				Params:    map[string]any{"temperature": 0},
			})
			if err != nil {
				t.Fatalf("expected mismatch not to consume exchange, got %v", err)
			}
			assertResponseText(t, response, "first response")
		})
	}
}

func TestReplayIndexRejectsExtraRequestAfterExhaustion(t *testing.T) {
	index := newTestReplayIndex(t)

	for _, inputHash := range []string{"sha256:first", "sha256:second"} {
		if _, err := index.MatchLLM(LLMRequest{
			Provider:  "openai",
			Model:     "gpt-4.1-mini",
			InputHash: inputHash,
			Params:    map[string]any{"temperature": 0},
		}); err != nil {
			t.Fatalf("MatchLLM returned error: %v", err)
		}
	}

	_, err := index.MatchLLM(LLMRequest{
		Provider:  "openai",
		Model:     "gpt-4.1-mini",
		InputHash: "sha256:extra",
		Params:    map[string]any{"temperature": 0},
	})
	if err == nil {
		t.Fatal("expected exhaustion error")
	}
	if !strings.Contains(err.Error(), "replay exhausted") {
		t.Fatalf("expected exhaustion error, got %q", err.Error())
	}
}

func newTestReplayIndex(t *testing.T) *ReplayIndex {
	t.Helper()

	events := readValidCassetteEvents(t,
		`{"schema_version":"0.1","event":"trace.start","trace_id":"tr_replay","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:first","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"first response"}}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_2","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:second","params":{"temperature":0}}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_2","output":{"text":"second response"}}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"tr_replay","status":"success"}`,
	)

	index, err := NewReplayIndex(events)
	if err != nil {
		t.Fatalf("NewReplayIndex returned error: %v", err)
	}
	return index
}

func readValidCassetteEvents(t *testing.T, lines ...string) []Event {
	t.Helper()

	path := writeTempCassette(t, strings.Join(lines, "\n"))
	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if !report.Valid() {
		t.Fatalf("expected valid cassette, got issues: %#v", report.Issues)
	}

	events, err := ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile returned error: %v", err)
	}
	return events
}

func assertResponseText(t *testing.T, response Event, text string) {
	t.Helper()

	output, ok := response.Raw["output"].(map[string]any)
	if !ok {
		t.Fatalf("expected output object, got %#v", response.Raw["output"])
	}
	if output["text"] != text {
		t.Fatalf("expected response text %q, got %#v", text, output["text"])
	}
}
