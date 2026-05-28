package cassette

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGeneratePytestTestsWritesReplayCaseForEachCassette(t *testing.T) {
	root := t.TempDir()
	cassettePath := filepath.Join(root, "traces", "run.replay.jsonl")
	outputPath := filepath.Join(root, "tests", "test_agent_replays.py")
	writeCassetteFile(t, cassettePath, minimalTestgenCassette("tr_test", "ok"))

	body, err := GeneratePytestTests(PytestGenerationOptions{
		CassettePaths: []string{cassettePath},
		OutputPath:    outputPath,
	})
	if err != nil {
		t.Fatalf("GeneratePytestTests returned error: %v", err)
	}

	generated := string(body)
	assertContains(t, generated, "from agentreplay.pytest import replay_case")
	assertContains(t, generated, "_prefer_local_agentreplay()\nfrom agentreplay.pytest import replay_case")
	assertContains(t, generated, `pytest.param(_HERE / "../traces/run.replay.jsonl", id="run")`)
	assertContains(t, generated, `@pytest.mark.parametrize("cassette", CASSETTES)`)
	assertContains(t, generated, `result = replay_case(cassette)`)
	assertContains(t, generated, `assert result.divergence_count == 0`)
}

func TestGeneratePytestTestsWritesOneParamPerCassette(t *testing.T) {
	root := t.TempDir()
	firstPath := filepath.Join(root, "traces", "first.replay.jsonl")
	secondPath := filepath.Join(root, "traces", "nested", "second.replay.jsonl")
	outputPath := filepath.Join(root, "tests", "test_agent_replays.py")
	writeCassetteFile(t, firstPath, minimalTestgenCassette("tr_first", "first"))
	writeCassetteFile(t, secondPath, minimalTestgenCassette("tr_second", "second"))

	body, err := GeneratePytestTests(PytestGenerationOptions{
		CassettePaths: []string{firstPath, secondPath},
		OutputPath:    outputPath,
	})
	if err != nil {
		t.Fatalf("GeneratePytestTests returned error: %v", err)
	}

	generated := string(body)
	assertContains(t, generated, `pytest.param(_HERE / "../traces/first.replay.jsonl", id="first")`)
	assertContains(t, generated, `pytest.param(_HERE / "../traces/nested/second.replay.jsonl", id="second")`)
}

func TestGeneratePytestTestsRejectsInvalidCassette(t *testing.T) {
	cassettePath := writeTempCassette(t, `{"schema_version":"0.1","event":"llm.call","span_id":"sp_1"}`+"\n")
	outputPath := filepath.Join(t.TempDir(), "test_agent_replays.py")

	_, err := GeneratePytestTests(PytestGenerationOptions{
		CassettePaths: []string{cassettePath},
		OutputPath:    outputPath,
	})
	if err == nil {
		t.Fatal("expected invalid cassette error")
	}
	assertContains(t, err.Error(), "is not a valid cassette")
}

func minimalTestgenCassette(traceID string, output string) string {
	return strings.Join([]string{
		`{"schema_version":"0.1","event":"trace.start","trace_id":"` + traceID + `","name":"unit"}`,
		`{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:abc"}`,
		`{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"` + output + `"}}`,
		`{"schema_version":"0.1","event":"trace.end","trace_id":"` + traceID + `","status":"success"}`,
	}, "\n")
}

func writeCassetteFile(t *testing.T, path string, body string) {
	t.Helper()

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("create cassette dir: %v", err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write cassette: %v", err)
	}
}
