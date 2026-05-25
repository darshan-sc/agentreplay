# Cassette Format

AgentReplay cassettes are versioned JSONL files with one event per line. They are append-only, portable, and intended to be human-readable enough to review in a diff.

## File Extension

```text
*.replay.jsonl
```

## Required Event Fields

Every event must include:

```json
{"schema_version":"0.1","event":"trace.start"}
```

## Event Types

- `trace.start`
- `llm.call`
- `llm.response`
- `tool.call`
- `tool.response`
- `retrieval.call`
- `retrieval.response`
- `agent.step`
- `error`
- `trace.end`

## Minimal Example

```jsonl
{"schema_version":"0.1","event":"trace.start","trace_id":"tr_123","name":"paper_qa","metadata":{"framework":"langgraph","prompt_version":"rag-v1"}}
{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:abc","params":{"temperature":0}}
{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"The paper argues..."},"usage":{"input_tokens":842,"output_tokens":136},"latency_ms":1180}
{"schema_version":"0.1","event":"trace.end","trace_id":"tr_123","status":"success","output_hash":"sha256:def"}
```

## Validation Rules

The validator enforces:

- Every line is a valid JSON event.
- `schema_version` is `0.1`.
- `event` is a known event type.
- The first event is `trace.start`.
- The final event is `trace.end`.
- Required fields exist for each event type.
- Events do not appear after `trace.end`.
- LLM, tool, and retrieval response events have a prior matching call event.
- LLM, tool, and retrieval call events receive a matching response.
- Active spans cannot be reused across event kinds.

## Writer and Hashing

The Go cassette package includes a JSONL writer that injects `schema_version` when omitted and validates each event before writing it.

`HashValue` and `HashJSON` produce `sha256:` hashes over canonical JSON so equivalent JSON objects with different whitespace or key order hash identically.

Future slices will add schema migration and broader runtime adapters.
