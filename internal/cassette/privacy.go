package cassette

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strings"
	"unicode"
)

type PrivacyMode string

const (
	PrivacySafe      PrivacyMode = "safe"
	PrivacyHideAll   PrivacyMode = "hide_all"
	PrivacyTransform PrivacyMode = "transform"
)

type PrivacyOptions struct {
	Mode      PrivacyMode
	Sanitizer func(any) any
}

const redactedValue = "[REDACTED]"

var hiddenPayload = map[string]any{"value_hidden": true}

var safeEnvelopeFields = map[string]struct{}{
	"schema_version": {},
	"event":          {},
	"trace_id":       {},
	"span_id":        {},
	"name":           {},
	"provider":       {},
	"model":          {},
	"status":         {},
	"latency_ms":     {},
}

var sensitiveExactKeys = map[string]struct{}{
	"api_key":           {},
	"api_token":         {},
	"authorization":     {},
	"bearer":            {},
	"client_secret":     {},
	"cookie":            {},
	"cookies":           {},
	"csrf_token":        {},
	"id_token":          {},
	"password":          {},
	"passwd":            {},
	"private_key":       {},
	"pwd":               {},
	"refresh_token":     {},
	"secret":            {},
	"secret_access_key": {},
	"secret_key":        {},
	"session_cookie":    {},
	"session_token":     {},
	"set_cookie":        {},
	"token":             {},
}

var sensitiveKeyParts = []string{
	"access_key",
	"api_key",
	"api_token",
	"authorization",
	"client_secret",
	"cookie",
	"csrf_token",
	"credential",
	"id_token",
	"password",
	"private_key",
	"refresh_token",
	"secret_access_key",
	"secret_key",
	"session_token",
}

var secretPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?s)-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----.*?-----END (?:[A-Z0-9 ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----`),
	regexp.MustCompile(`(?i)\b(?:set-cookie|cookie)\s*:\s*[^,\n]+`),
	regexp.MustCompile(`(?i)\b[^=\s;]+=[^;\s]+;\s*(?:HttpOnly|Secure|SameSite=[A-Za-z]+)`),
	regexp.MustCompile(`(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+`),
	regexp.MustCompile(`(?i)(?m)(^|[^A-Za-z0-9_])["']?(?:aws_secret_access_key|secret_access_key|access_key_secret|client_secret|api_key|api_token|access_token|refresh_token|id_token|session_token|csrf_token|token|password|passwd|pwd)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,\s;&}\]]+)`),
	regexp.MustCompile(`\b(?:AKIA|ASIA|AIDA|AROA|AGPA|AIPA|ANPA)[A-Z0-9]{16}\b`),
	regexp.MustCompile(`sk-[A-Za-z0-9_-]{8,}`),
	regexp.MustCompile(`(?i)Bearer\s+[A-Za-z0-9._-]+`),
}

func sanitizeEvent(fields map[string]any, options PrivacyOptions) (map[string]any, error) {
	mode := options.Mode
	if mode == "" {
		mode = PrivacySafe
	}

	switch mode {
	case PrivacySafe:
		sanitized, ok := sanitizeValue(fields).(map[string]any)
		if !ok {
			return nil, fmt.Errorf("sanitize cassette event: expected object")
		}
		return sanitized, nil
	case PrivacyTransform:
		if options.Sanitizer == nil {
			return nil, fmt.Errorf("privacy mode %q requires a sanitizer", PrivacyTransform)
		}
		sanitized, ok := sanitizeValue(options.Sanitizer(fields)).(map[string]any)
		if !ok {
			return nil, fmt.Errorf("sanitize cassette event: expected object")
		}
		return sanitized, nil
	case PrivacyHideAll:
		return hideEventPayload(fields), nil
	default:
		return nil, fmt.Errorf("unknown privacy mode %q", mode)
	}
}

func hideEventPayload(fields map[string]any) map[string]any {
	hidden := map[string]any{}
	for key, value := range fields {
		if _, ok := safeEnvelopeFields[key]; !ok {
			continue
		}
		hidden[key] = sanitizeValue(value)
	}

	eventType, _ := fields["event"].(string)
	switch eventType {
	case "llm.call":
		hidden["input_hash"] = "hidden:payload"
	case "llm.response", "tool.response":
		if _, ok := fields["error"]; ok {
			hidden["error"] = "[HIDDEN]"
		} else {
			hidden["output"] = cloneMap(hiddenPayload)
		}
	case "agent.step":
		hidden["output"] = cloneMap(hiddenPayload)
	case "retrieval.call":
		hidden["query"] = "[HIDDEN]"
	case "retrieval.response":
		hidden["documents"] = []any{}
	case "error":
		hidden["message"] = "[HIDDEN]"
	}
	return hidden
}

func sanitizeValue(value any) any {
	switch typed := value.(type) {
	case nil:
		return nil
	case string:
		return redactText(typed)
	case bool:
		return typed
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, json.Number:
		return typed
	case float32:
		if math.IsInf(float64(typed), 0) || math.IsNaN(float64(typed)) {
			return unsupportedPayloadMarker()
		}
		return typed
	case float64:
		if math.IsInf(typed, 0) || math.IsNaN(typed) {
			return unsupportedPayloadMarker()
		}
		return typed
	case map[string]any:
		return sanitizeStringMap(typed)
	case map[any]any:
		result := map[string]any{}
		for key, item := range typed {
			keyText := fmt.Sprint(key)
			if shouldDropKey(keyText) || redactText(keyText) != keyText {
				continue
			}
			result[keyText] = sanitizeValue(item)
		}
		return result
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, sanitizeValue(item))
		}
		return result
	}

	rv := reflect.ValueOf(value)
	if rv.IsValid() && rv.Kind() == reflect.Map {
		result := map[string]any{}
		iter := rv.MapRange()
		for iter.Next() {
			key := iter.Key()
			if !key.CanInterface() {
				return unsupportedPayloadMarker()
			}
			keyText, ok := mapKeyText(key.Interface())
			if !ok {
				return unsupportedPayloadMarker()
			}
			if shouldDropKey(keyText) || redactText(keyText) != keyText {
				continue
			}
			value := iter.Value()
			if !value.CanInterface() {
				return unsupportedPayloadMarker()
			}
			result[keyText] = sanitizeValue(value.Interface())
		}
		return result
	}
	if rv.IsValid() && (rv.Kind() == reflect.Slice || rv.Kind() == reflect.Array) {
		result := make([]any, 0, rv.Len())
		for i := 0; i < rv.Len(); i++ {
			result = append(result, sanitizeValue(rv.Index(i).Interface()))
		}
		return result
	}
	return unsupportedPayloadMarker()
}

func mapKeyText(key any) (string, bool) {
	switch typed := key.(type) {
	case string:
		return typed, true
	case fmt.Stringer:
		return typed.String(), true
	default:
		return "", false
	}
}

func sanitizeStringMap(value map[string]any) map[string]any {
	result := map[string]any{}
	for key, item := range value {
		if shouldDropKey(key) || redactText(key) != key {
			continue
		}
		result[key] = sanitizeValue(item)
	}
	return result
}

func shouldDropKey(key string) bool {
	normalized := normalizeKey(key)
	if _, ok := sensitiveExactKeys[normalized]; ok {
		return true
	}
	if strings.HasPrefix(normalized, "secret_") ||
		strings.HasPrefix(normalized, "token_") {
		return true
	}
	if strings.HasSuffix(normalized, "_password") ||
		strings.HasSuffix(normalized, "_secret") ||
		strings.HasSuffix(normalized, "_token") {
		return true
	}
	for _, part := range sensitiveKeyParts {
		if strings.Contains(normalized, part) {
			return true
		}
	}
	return false
}

func normalizeKey(key string) string {
	runes := []rune(key)
	var builder strings.Builder
	for index, current := range runes {
		if index > 0 && shouldInsertKeySeparator(runes, index) {
			builder.WriteByte('_')
		}
		if unicode.IsLetter(current) || unicode.IsDigit(current) {
			builder.WriteRune(unicode.ToLower(current))
		} else {
			builder.WriteByte('_')
		}
	}
	parts := strings.FieldsFunc(builder.String(), func(r rune) bool { return r == '_' })
	return strings.Join(parts, "_")
}

func shouldInsertKeySeparator(runes []rune, index int) bool {
	current := runes[index]
	previous := runes[index-1]
	if !unicode.IsUpper(current) {
		return false
	}
	if unicode.IsLower(previous) || unicode.IsDigit(previous) {
		return true
	}
	if unicode.IsUpper(previous) && index+1 < len(runes) && unicode.IsLower(runes[index+1]) {
		return true
	}
	return false
}

func redactText(text string) string {
	redacted := text
	for _, pattern := range secretPatterns {
		redacted = pattern.ReplaceAllString(redacted, redactedValue)
	}
	return redacted
}

func unsupportedPayloadMarker() map[string]any {
	return map[string]any{
		"value_unavailable": true,
		"reason":            "unsupported_type",
	}
}

func cloneMap(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}
