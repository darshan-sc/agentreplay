package cassette

import (
	"regexp"
	"strings"
	"testing"
)

func TestHashJSONCanonicalizesWhitespaceAndKeyOrder(t *testing.T) {
	first, err := HashJSON([]byte(`{"a":1,"b":2}`))
	if err != nil {
		t.Fatalf("HashJSON returned error: %v", err)
	}
	second, err := HashJSON([]byte("{\n  \"b\": 2,\n  \"a\": 1\n}"))
	if err != nil {
		t.Fatalf("HashJSON returned error: %v", err)
	}

	if first != second {
		t.Fatalf("expected equivalent JSON to hash the same, got %q and %q", first, second)
	}
	assertHashFormat(t, first)
}

func TestHashValueCanonicalizesNestedMaps(t *testing.T) {
	first, err := HashValue(map[string]any{
		"messages": []any{
			map[string]any{
				"role":    "user",
				"content": "hello",
			},
		},
		"params": map[string]any{
			"temperature": 0,
			"top_p":       1,
		},
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}

	second, err := HashValue(map[string]any{
		"params": map[string]any{
			"top_p":       1,
			"temperature": 0,
		},
		"messages": []any{
			map[string]any{
				"content": "hello",
				"role":    "user",
			},
		},
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}

	if first != second {
		t.Fatalf("expected nested maps to hash deterministically, got %q and %q", first, second)
	}
}

func TestHashJSONRejectsInvalidJSON(t *testing.T) {
	_, err := HashJSON([]byte(`{"a":`))
	if err == nil {
		t.Fatal("expected invalid JSON to return an error")
	}
	if !strings.Contains(err.Error(), "decode JSON for hash") {
		t.Fatalf("expected decode error, got %q", err.Error())
	}
}

func TestHashJSONRejectsMultipleValues(t *testing.T) {
	_, err := HashJSON([]byte(`{"a":1} {"b":2}`))
	if err == nil {
		t.Fatal("expected multiple JSON values to return an error")
	}
	if !strings.Contains(err.Error(), "multiple JSON values") {
		t.Fatalf("expected multiple values error, got %q", err.Error())
	}
}

func TestHashChangesWhenInputChanges(t *testing.T) {
	first, err := HashValue(map[string]any{
		"model": "gpt-4.1-mini",
		"input": []any{
			map[string]any{"role": "user", "content": "hello"},
		},
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}
	second, err := HashValue(map[string]any{
		"model": "gpt-4.1-mini",
		"input": []any{
			map[string]any{"role": "user", "content": "hello!"},
		},
	})
	if err != nil {
		t.Fatalf("HashValue returned error: %v", err)
	}

	if first == second {
		t.Fatalf("expected changed input to change hash, got %q", first)
	}
}

func assertHashFormat(t *testing.T, hash string) {
	t.Helper()

	matched, err := regexp.MatchString(`^sha256:[0-9a-f]{64}$`, hash)
	if err != nil {
		t.Fatalf("regexp failed: %v", err)
	}
	if !matched {
		t.Fatalf("expected sha256 hash format, got %q", hash)
	}
}
