# agentreplay

Deterministic replay and regression tests for LLM agents.

AgentReplay records an agent run into a portable `.replay.jsonl` cassette, replays it offline, diffs behavior, and generates pytest regression tests.

## Status

This repo has the first cassette layer and three CLI commands wired. It can read, write, validate, inspect, diff, and hash `.replay.jsonl` cassettes, and it has an in-process LLM replay index for matching recorded exchanges. Runtime recording, the `replay` CLI flow, and pytest generation are planned next.

## Quickstart

```bash
go test ./...
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
- Synthetic sample cassette

## Not Implemented Yet

- Recording live OpenAI calls.
- Python OpenAI hook/runtime integration.
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
