package cassette

import (
	"bytes"
	"errors"
	"strings"
	"testing"
)

func TestWriterWritesValidCassette(t *testing.T) {
	var buf bytes.Buffer
	writer := NewWriter(&buf)

	events := []map[string]any{
		{
			"event":    "trace.start",
			"trace_id": "tr_writer",
			"name":     "writer_test",
			"metadata": map[string]any{
				"framework": "unit",
			},
		},
		{
			"event":      "llm.call",
			"span_id":    "sp_1",
			"provider":   "openai",
			"model":      "gpt-4.1-mini",
			"input_hash": "sha256:abc",
			"params": map[string]any{
				"temperature": 0,
			},
		},
		{
			"event":   "llm.response",
			"span_id": "sp_1",
			"output": map[string]any{
				"text": "ok",
			},
			"usage": map[string]any{
				"input_tokens":  1,
				"output_tokens": 1,
			},
		},
		{
			"event":    "trace.end",
			"trace_id": "tr_writer",
			"status":   "success",
		},
	}

	for _, event := range events {
		if err := writer.WriteEvent(event); err != nil {
			t.Fatalf("WriteEvent returned error: %v", err)
		}
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}

	path := writeTempCassette(t, buf.String())
	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if !report.Valid() {
		t.Fatalf("expected valid report, got issues: %#v", report.Issues)
	}

	readEvents, err := ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile returned error: %v", err)
	}
	if _, ok := readEvents[0].Raw["metadata"].(map[string]any); !ok {
		t.Fatalf("expected metadata to survive readback, got %#v", readEvents[0].Raw["metadata"])
	}
	if _, ok := readEvents[1].Raw["params"].(map[string]any); !ok {
		t.Fatalf("expected params to survive readback, got %#v", readEvents[1].Raw["params"])
	}
	if _, ok := readEvents[2].Raw["output"].(map[string]any); !ok {
		t.Fatalf("expected output to survive readback, got %#v", readEvents[2].Raw["output"])
	}
	if _, ok := readEvents[2].Raw["usage"].(map[string]any); !ok {
		t.Fatalf("expected usage to survive readback, got %#v", readEvents[2].Raw["usage"])
	}
}

func TestWriterInjectsSchemaVersionWithoutMutatingInput(t *testing.T) {
	var buf bytes.Buffer
	writer := NewWriter(&buf)
	event := map[string]any{
		"event":    "trace.start",
		"trace_id": "tr_writer",
		"name":     "writer_test",
	}

	if err := writer.WriteEvent(event); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}

	if _, ok := event["schema_version"]; ok {
		t.Fatal("WriteEvent mutated caller's map")
	}
	if !strings.Contains(buf.String(), `"schema_version":"0.1"`) {
		t.Fatalf("expected writer to inject schema version, got %q", buf.String())
	}
}

func TestWriterRejectsInvalidEnvelope(t *testing.T) {
	tests := []struct {
		name   string
		event  map[string]any
		errMsg string
	}{
		{
			name:   "missing event",
			event:  map[string]any{"trace_id": "tr_writer"},
			errMsg: "missing event",
		},
		{
			name:   "blank event",
			event:  map[string]any{"event": ""},
			errMsg: "event must be a non-empty string",
		},
		{
			name:   "unsupported schema",
			event:  map[string]any{"schema_version": "0.2", "event": "trace.start"},
			errMsg: "unsupported schema_version",
		},
		{
			name:   "unknown event",
			event:  map[string]any{"event": "unknown"},
			errMsg: "unknown event type",
		},
		{
			name:   "missing event-specific fields",
			event:  map[string]any{"event": "trace.start"},
			errMsg: "invalid trace.start event: missing trace_id",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var buf bytes.Buffer
			err := NewWriter(&buf).WriteEvent(tt.event)
			if err == nil {
				t.Fatal("expected error")
			}
			if !strings.Contains(err.Error(), tt.errMsg) {
				t.Fatalf("expected error containing %q, got %q", tt.errMsg, err.Error())
			}
			if buf.Len() != 0 {
				t.Fatalf("expected no bytes to be written, got %q", buf.String())
			}
		})
	}
}

func TestWriterWritesStableJSON(t *testing.T) {
	first := writeOneEvent(t, map[string]any{
		"event":    "trace.start",
		"name":     "writer_test",
		"trace_id": "tr_writer",
		"metadata": map[string]any{
			"z": "last",
			"a": "first",
		},
	})
	second := writeOneEvent(t, map[string]any{
		"metadata": map[string]any{
			"a": "first",
			"z": "last",
		},
		"trace_id": "tr_writer",
		"name":     "writer_test",
		"event":    "trace.start",
	})

	if first != second {
		t.Fatalf("expected stable JSON\nfirst:  %s\nsecond: %s", first, second)
	}
}

func TestWriterRedactsPayloadsBeforeWritingAndHashing(t *testing.T) {
	line := writeOneEvent(t, map[string]any{
		"event":   "tool.response",
		"span_id": "sp_1",
		"output": map[string]any{
			"message":           "Bearer writer-token.123",
			"apiKey":            "drop-me",
			"APIKey":            "drop-acronym-api-key",
			"IDToken":           "drop-acronym-id-token",
			"CSRFToken":         "drop-acronym-csrf-token",
			"secret":            "drop-secret",
			"token":             "drop-token",
			"max_output_tokens": 16,
			"input_tokens":      8,
		},
	})

	if strings.Contains(line, "writer-token") ||
		strings.Contains(line, "drop-me") ||
		strings.Contains(line, "drop-secret") ||
		strings.Contains(line, "drop-token") ||
		strings.Contains(line, "drop-acronym") ||
		strings.Contains(line, "apiKey") {
		t.Fatalf("expected secrets to be absent from written line, got %q", line)
	}
	if !strings.Contains(line, `"message":"[REDACTED]"`) {
		t.Fatalf("expected redacted message, got %q", line)
	}

	expectedHash, err := HashValue(map[string]any{
		"message":           "[REDACTED]",
		"max_output_tokens": 16,
		"input_tokens":      8,
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}
	if !strings.Contains(line, `"output_hash":"`+expectedHash+`"`) {
		t.Fatalf("expected output_hash from sanitized output %q, got %q", expectedHash, line)
	}
}

func TestWriterPreservesTypedNestedMaps(t *testing.T) {
	line := writeOneEvent(t, map[string]any{
		"event":    "trace.start",
		"trace_id": "tr_typed_map",
		"name":     "typed_map",
		"metadata": map[string]string{
			"framework": "unit",
			"token":     "drop-token",
		},
	})

	if !strings.Contains(line, `"metadata":{"framework":"unit"}`) {
		t.Fatalf("expected typed map payload to survive sanitization, got %q", line)
	}
	if strings.Contains(line, "drop-token") {
		t.Fatalf("expected typed map secret to be dropped, got %q", line)
	}
}

func TestWriterHideAllSuppressesPayloadsAndContentHashes(t *testing.T) {
	var buf bytes.Buffer
	writer := NewWriterWithPrivacy(&buf, PrivacyOptions{Mode: PrivacyHideAll})

	events := []map[string]any{
		{
			"event":    "trace.start",
			"trace_id": "tr_hidden",
			"name":     "hidden",
			"metadata": map[string]any{"prompt": "secret"},
		},
		{
			"event":      "llm.call",
			"trace_id":   "tr_hidden",
			"span_id":    "sp_1",
			"provider":   "openai",
			"model":      "gpt-4.1-mini",
			"input_hash": "sha256:secret",
			"params":     map[string]any{"temperature": 0},
		},
		{
			"event":    "llm.response",
			"trace_id": "tr_hidden",
			"span_id":  "sp_1",
			"output":   map[string]any{"text": "secret"},
		},
		{
			"event":       "trace.end",
			"trace_id":    "tr_hidden",
			"status":      "success",
			"output_hash": "sha256:secret",
		},
	}
	for _, event := range events {
		if err := writer.WriteEvent(event); err != nil {
			t.Fatalf("WriteEvent returned error: %v", err)
		}
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}

	output := buf.String()
	if strings.Contains(output, "sha256:secret") || strings.Contains(output, `"params"`) || strings.Contains(output, `"metadata"`) {
		t.Fatalf("expected hidden cassette to suppress payload details, got %q", output)
	}
	if !strings.Contains(output, `"input_hash":"hidden:payload"`) {
		t.Fatalf("expected hidden input hash placeholder, got %q", output)
	}
	if !strings.Contains(output, `"output":{"value_hidden":true}`) {
		t.Fatalf("expected hidden output placeholder, got %q", output)
	}

	path := writeTempCassette(t, output)
	report, err := ValidateFile(path)
	if err != nil {
		t.Fatalf("ValidateFile returned error: %v", err)
	}
	if !report.Valid() {
		t.Fatalf("expected valid hidden cassette, got issues: %#v", report.Issues)
	}
}

func TestWriterFlushPropagatesErrors(t *testing.T) {
	expected := errors.New("write failed")
	writer := NewWriter(failingWriter{err: expected})

	if err := writer.WriteEvent(map[string]any{
		"event":    "trace.start",
		"trace_id": "tr_writer",
		"name":     "writer_test",
	}); err != nil {
		t.Fatalf("WriteEvent returned error before flush: %v", err)
	}
	if err := writer.Flush(); !errors.Is(err, expected) {
		t.Fatalf("expected flush error %v, got %v", expected, err)
	}
}

func writeOneEvent(t *testing.T, event map[string]any) string {
	t.Helper()

	var buf bytes.Buffer
	writer := NewWriter(&buf)
	if err := writer.WriteEvent(event); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}
	return buf.String()
}

type failingWriter struct {
	err error
}

func (w failingWriter) Write([]byte) (int, error) {
	return 0, w.err
}
