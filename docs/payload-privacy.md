# Payload Privacy

AgentReplay sanitizes cassette payloads at the recorder boundary before JSON is written to disk. The default policy is designed to keep cassettes useful for replay, inspection, and diffing without persisting common secrets.

## Modes

- `safe`: default. Recursively redacts known secret values, drops secret-bearing keys, preserves benign runtime fields, and hashes the sanitized payload.
- `hide_all`: preserves event structure, trace IDs, span IDs, event types, and required validation placeholders while suppressing payload content and content-derived output hashes.
- `transform`: applies a caller-supplied sanitizer before serialization, then writes and hashes the sanitized result.

## Protected Data

The sanitizer drops or redacts common API keys, bearer tokens, cloud credentials, credential URLs, cookie strings, private-key blocks, JSON-style secret assignments, nested secret-like values, and secret-bearing exception messages or object payloads.

Secret-key matching handles common naming variants such as `api_key`, `api-key`, `apiKey`, `ApiKey`, and `AWS_SECRET_ACCESS_KEY`. Benign runtime fields such as `max_output_tokens`, `input_tokens`, `output_tokens`, and `total_tokens` are preserved.

## Recorder Contract

Recorder implementations must sanitize before serializing, sanitize before hashing, hide before writing, and prefer safe placeholders over teardown failures for successful tool outputs that are not naturally JSON serializable.

`trace.end.output_hash` reflects the last sanitized output-bearing event in the trace, including tool-only and agent-step-only runs.
