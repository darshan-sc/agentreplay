# agentreplay

Deterministic replay and regression tests for LLM agents.

AgentReplay records an agent run into a portable `.replay.jsonl` cassette, replays it offline, diffs behavior, and generates pytest regression tests.

## Status

This repo has the first cassette layer, three CLI commands, and a narrow Python OpenAI recording hook wired. It can read, write, validate, inspect, diff, and hash `.replay.jsonl` cassettes, record non-streaming `client.responses.create(...)` calls, and match recorded LLM exchanges in process. Offline runtime replay, the `record`/`replay` CLI flow, and pytest generation are planned next.

## Quickstart

```bash
go test ./...
python3 -m unittest discover -s python/tests
go run ./cmd/agentreplay validate traces/sample.replay.jsonl
go run ./cmd/agentreplay inspect traces/sample.replay.jsonl
go run ./cmd/agentreplay diff traces/sample.replay.jsonl traces/sample.replay.jsonl
```

Expected validation output:

```text
OK: traces/sample.replay.jsonl (4 events)
```

## v0.1 Scope

- Go CLI with `record`, `replay`, `diff`, `generate-tests`, `validate`, and `inspect`.
- Versioned JSONL cassette format.
- Thin Python hook for OpenAI non-streaming calls.
- One LangGraph demo agent that records and replays offline.
- Pytest generator for regression tests.

## Implemented Now

- `agentreplay validate`
- `agentreplay inspect`
- `agentreplay diff`
- Versioned JSONL cassette reader and writer
- Cassette validator with trace/span consistency checks
- Deterministic JSON hash helpers
- In-process LLM replay index and request matching
- Python OpenAI non-streaming recording hook for `client.responses.create(...)`
- Synthetic sample cassette

## Not Implemented Yet

- `agentreplay record`.
- `agentreplay replay` runtime flow.
- Pytest generation.
- LangGraph demo code.

## Not in v0.1

- Dashboard.
- Server or hosted service.
- Postgres or Redis.
- Auth.
- Streaming replay.
- Broad framework adapter support.

## Docs

- [Cassette format](docs/cassette-format.md)
- [Replay semantics](docs/replay-semantics.md)
- [Architecture](docs/architecture.md)
