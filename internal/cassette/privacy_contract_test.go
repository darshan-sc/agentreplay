package cassette

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

var secretKeyVariants = []string{
	"api_key",
	"api-key",
	"apiKey",
	"ApiKey",
	"APIKey",
	"access_key",
	"accessKey",
	"private_key",
	"privateKey",
	"client_secret",
	"secret_access_key",
	"AWS_SECRET_ACCESS_KEY",
	"secret_key",
	"session_token",
	"csrf_token",
	"CSRFToken",
	"id_token",
	"IDToken",
	"password",
	"passwd",
	"pwd",
	"cookie",
	"cookies",
	"set-cookie",
	"session_cookie",
	"authorization",
	"bearer",
	"api_token",
	"refresh_token",
	"token",
	"secret",
	"tokenValue",
	"secretValue",
}

var benignKeyVariants = []string{
	"max_output_tokens",
	"input_tokens",
	"output_tokens",
	"total_tokens",
	"temperature",
}

var secretValueCases = map[string]string{
	"openai":            "sk-contractsecret123456",
	"bearer":            "Bearer contract-token.123",
	"credential_url":    "postgres://user:pass@db/app",
	"env_assignment":    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
	"json_assignment":   `{"password":"hunter2","safe":"ok"}`,
	"cookie_header":     "Cookie: sid=abc123; HttpOnly",
	"private_key":       "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----",
	"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
}

func TestPrivacyContractSafeModeKeyCorpus(t *testing.T) {
	payload := map[string]any{}
	for index, key := range secretKeyVariants {
		payload[key] = "drop-" + string(rune('a'+index%26))
	}
	for index, key := range benignKeyVariants {
		payload[key] = index
	}

	sanitized, ok := sanitizeValue(payload).(map[string]any)
	if !ok {
		t.Fatalf("expected sanitized map, got %#v", sanitized)
	}

	for _, key := range secretKeyVariants {
		if _, ok := sanitized[key]; ok {
			t.Fatalf("expected secret key %q to be dropped from %#v", key, sanitized)
		}
	}
	for index, key := range benignKeyVariants {
		if sanitized[key] != index {
			t.Fatalf("expected benign key %q to be preserved with %d, got %#v", key, index, sanitized[key])
		}
	}
}

func TestPrivacyContractSafeModeValueCorpus(t *testing.T) {
	payload := map[string]any{
		"cases": secretValueCases,
		"safe":  "ok",
	}

	sanitized := sanitizeValue(payload)
	raw, err := json.Marshal(sanitized)
	if err != nil {
		t.Fatalf("json.Marshal returned error: %v", err)
	}
	text := string(raw)

	if !strings.Contains(text, `"safe":"ok"`) {
		t.Fatalf("expected safe field to survive, got %s", text)
	}
	for _, value := range secretValueCases {
		if strings.Contains(text, value) {
			t.Fatalf("expected secret value %q to be redacted from %s", value, text)
		}
	}
	for _, fragment := range []string{"hunter2", "user:pass", "OPENSSH PRIVATE KEY"} {
		if strings.Contains(text, fragment) {
			t.Fatalf("expected secret fragment %q to be absent from %s", fragment, text)
		}
	}
	if !strings.Contains(text, redactedValue) {
		t.Fatalf("expected redaction marker in %s", text)
	}
}

func TestPrivacyContractWriterHashesSanitizedPayload(t *testing.T) {
	line := writeOneEvent(t, map[string]any{
		"event":    "tool.response",
		"trace_id": "tr_contract",
		"span_id":  "sp_1",
		"output": map[string]any{
			"safe":    "ok",
			"token":   "drop-token",
			"message": "Bearer contract-token.123",
		},
	})

	expectedHash, err := HashValue(map[string]any{
		"safe":    "ok",
		"message": redactedValue,
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}
	for _, fragment := range []string{"drop-token", "contract-token"} {
		if strings.Contains(line, fragment) {
			t.Fatalf("expected %q to be absent from %q", fragment, line)
		}
	}
	if !strings.Contains(line, `"output_hash":"`+expectedHash+`"`) {
		t.Fatalf("expected sanitized output hash %q in %q", expectedHash, line)
	}
}

func TestPrivacyContractHideAllStructure(t *testing.T) {
	var buf bytes.Buffer
	writer := NewWriterWithPrivacy(&buf, PrivacyOptions{Mode: PrivacyHideAll})

	if err := writer.WriteEvent(map[string]any{
		"event":      "llm.call",
		"trace_id":   "tr_contract",
		"span_id":    "sp_1",
		"provider":   "openai",
		"model":      "gpt-4.1-mini",
		"input_hash": "sha256:secret",
		"params":     map[string]any{"temperature": 0},
	}); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.WriteEvent(map[string]any{
		"event":    "llm.response",
		"trace_id": "tr_contract",
		"span_id":  "sp_1",
		"output":   map[string]any{"text": "secret output"},
	}); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}

	output := buf.String()
	if strings.Contains(output, "secret") || strings.Contains(output, `"params"`) || strings.Contains(output, `"output_hash"`) {
		t.Fatalf("expected hidden output to omit payload details, got %q", output)
	}
	if !strings.Contains(output, `"input_hash":"hidden:payload"`) {
		t.Fatalf("expected hidden input hash placeholder, got %q", output)
	}
	if !strings.Contains(output, `"output":{"value_hidden":true}`) {
		t.Fatalf("expected hidden output placeholder, got %q", output)
	}
}

func TestPrivacyContractHideAllPreservesResponseErrors(t *testing.T) {
	line := writeOneEventWithPrivacy(t, PrivacyOptions{Mode: PrivacyHideAll}, map[string]any{
		"event":    "llm.response",
		"trace_id": "tr_contract",
		"span_id":  "sp_1",
		"error":    "RuntimeError: secret failure",
	})

	if !strings.Contains(line, `"error":"[HIDDEN]"`) {
		t.Fatalf("expected hidden error marker, got %q", line)
	}
	if strings.Contains(line, `"output"`) || strings.Contains(line, "secret failure") {
		t.Fatalf("expected hidden error to preserve error semantics without output/details, got %q", line)
	}
}

func TestPrivacyContractTransformHashesTransformedPayload(t *testing.T) {
	var buf bytes.Buffer
	writer := NewWriterWithPrivacy(&buf, PrivacyOptions{
		Mode: PrivacyTransform,
		Sanitizer: func(value any) any {
			return replacePrivateValue(value)
		},
	})

	if err := writer.WriteEvent(map[string]any{
		"event":    "tool.response",
		"trace_id": "tr_contract",
		"span_id":  "sp_1",
		"output":   map[string]any{"safe": "private-value"},
	}); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}

	expectedHash, err := HashValue(map[string]any{"safe": "[PRIVATE]"})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}
	output := buf.String()
	if strings.Contains(output, "private-value") {
		t.Fatalf("expected raw private value to be absent from %q", output)
	}
	if !strings.Contains(output, `"output":{"safe":"[PRIVATE]"}`) {
		t.Fatalf("expected transformed output in %q", output)
	}
	if !strings.Contains(output, `"output_hash":"`+expectedHash+`"`) {
		t.Fatalf("expected transformed hash %q in %q", expectedHash, output)
	}
}

func writeOneEventWithPrivacy(t *testing.T, options PrivacyOptions, event map[string]any) string {
	t.Helper()

	var buf bytes.Buffer
	writer := NewWriterWithPrivacy(&buf, options)
	if err := writer.WriteEvent(event); err != nil {
		t.Fatalf("WriteEvent returned error: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("Flush returned error: %v", err)
	}
	return buf.String()
}

func replacePrivateValue(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		result := map[string]any{}
		for key, item := range typed {
			result[key] = replacePrivateValue(item)
		}
		return result
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, replacePrivateValue(item))
		}
		return result
	case string:
		if typed == "private-value" {
			return "[PRIVATE]"
		}
	}
	return value
}
