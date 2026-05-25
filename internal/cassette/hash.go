package cassette

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
)

const HashPrefix = "sha256:"

func HashValue(v any) (string, error) {
	canonical, err := canonicalJSON(v)
	if err != nil {
		return "", err
	}

	sum := sha256.Sum256(canonical)
	return HashPrefix + hex.EncodeToString(sum[:]), nil
}

func HashJSON(raw []byte) (string, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()

	var value any
	if err := decoder.Decode(&value); err != nil {
		return "", fmt.Errorf("decode JSON for hash: %w", err)
	}

	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return "", fmt.Errorf("decode JSON for hash: multiple JSON values")
		}
		return "", fmt.Errorf("decode JSON for hash: %w", err)
	}

	return HashValue(value)
}

func canonicalJSON(v any) ([]byte, error) {
	canonical, err := json.Marshal(v)
	if err != nil {
		return nil, fmt.Errorf("canonicalize JSON for hash: %w", err)
	}
	return canonical, nil
}
