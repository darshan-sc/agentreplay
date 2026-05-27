# AgentReplay

Deterministic replay for LLM agents.

AgentReplay records an agent run into a portable `.replay.jsonl` cassette, then lets you replay it offline, validate it, inspect it, and diff it against future runs. It turns flaky, expensive, hard-to-debug LLM workflows into concrete artifacts you can review in Git, run in CI, and use as regression tests.

## Why it matters

LLM agents are powerful because they can branch, call tools, retrieve context, and adapt. That same power makes them painful to test. A small prompt change, model change, SDK change, or hidden input change can quietly move the whole workflow.

AgentReplay gives you a simple loop:

1. Record a real run once.
2. Save the cassette.
3. Replay the same LLM responses offline.
4. Diff new behavior against known-good behavior.
5. Fail loudly when the workflow drifts.

No silent live fallback. No guessing what changed. Just a readable timeline of what the agent asked for, what it got back, and where a later run diverged.

## What it does today

- Records non-streaming OpenAI `client.responses.create(...)` calls from Python.
- Replays recorded OpenAI responses offline.
- Writes versioned JSONL cassettes that are easy to inspect and diff.
- Validates cassette structure, event order, spans, and trace consistency.
- Diffs two cassettes to expose replay-relevant changes.
- Provides a Go CLI for `record`, `replay`, `validate`, `inspect`, and `diff`.
- Provides Python context managers for direct OpenAI record/replay hooks.

## Quickstart

Run the test suite and inspect the sample cassette:

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

## Record and replay an OpenAI call

Put your OpenAI key in `.env.local`:

```bash
OPENAI_API_KEY=...
```

Record one live call:

```bash
go run ./cmd/agentreplay record --out tmp/openai-smoke.replay.jsonl -- python3 python/examples/openai_record_smoke.py
```

Replay the same workflow offline:

```bash
go run ./cmd/agentreplay replay tmp/openai-smoke.replay.jsonl -- python3 python/examples/openai_record_smoke.py
```

During replay, AgentReplay patches the OpenAI Responses API inside the process and returns the recorded response from the cassette. If the code asks for a different request, changes order, or cannot match the recorded call, replay fails instead of calling the live API.

## Python API

Use the hooks directly when you want record/replay behavior inside your own test or script.

```python
from openai import OpenAI
from agentreplay import recording_openai, replaying_openai

client = OpenAI()

with recording_openai("traces/run.replay.jsonl", name="smoke-test"):
    response = client.responses.create(
        model="gpt-4.1-mini",
        input="Reply with exactly: agentreplay-ok",
        temperature=0,
        max_output_tokens=16,
    )

with replaying_openai("traces/run.replay.jsonl"):
    response = client.responses.create(
        model="gpt-4.1-mini",
        input="Reply with exactly: agentreplay-ok",
        temperature=0,
        max_output_tokens=16,
    )
```

## CLI

```text
agentreplay records, replays, diffs, and tests LLM-agent runs.

Usage:
  agentreplay validate <cassette.replay.jsonl>
  agentreplay inspect <cassette.replay.jsonl>
  agentreplay record --out <cassette.replay.jsonl> -- <command> [args...]
  agentreplay replay <cassette.replay.jsonl> -- <command> [args...]
  agentreplay diff <before.replay.jsonl> <after.replay.jsonl>
```

The CLI passes these environment variables to the child process:

- `AGENTREPLAY_MODE=record` or `AGENTREPLAY_MODE=replay`
- `AGENTREPLAY_CASSETTE=<cassette path>`
- `AGENTREPLAY_RECORD_OUT=<cassette path>` during record
- `AGENTREPLAY_REPLAY_PATH=<cassette path>` during replay

## Cassette format

An AgentReplay cassette is JSONL: one event per line, versioned, append-only, and friendly to code review.

```jsonl
{"schema_version":"0.1","event":"trace.start","trace_id":"tr_123","name":"paper_qa","metadata":{"framework":"langgraph","prompt_version":"rag-v1"}}
{"schema_version":"0.1","event":"llm.call","span_id":"sp_1","provider":"openai","model":"gpt-4.1-mini","input_hash":"sha256:abc","params":{"temperature":0}}
{"schema_version":"0.1","event":"llm.response","span_id":"sp_1","output":{"text":"The paper argues..."},"usage":{"input_tokens":842,"output_tokens":136},"latency_ms":1180}
{"schema_version":"0.1","event":"trace.end","trace_id":"tr_123","status":"success","output_hash":"sha256:def"}
```

The format is intentionally framework-neutral. Go owns cassette validation, diffing, replay matching, and CLI orchestration. Python owns the first runtime hooks.

## Current scope

Implemented:

- `agentreplay validate`
- `agentreplay inspect`
- `agentreplay diff`
- `agentreplay record`
- `agentreplay replay`
- Versioned JSONL cassette reader and writer
- Cassette validator with trace/span consistency checks
- Deterministic JSON hash helpers
- In-process LLM replay index and request matching
- Python OpenAI non-streaming recording and replay hooks
- Synthetic sample cassette

Planned next:

- Pytest regression test generation
- LangGraph demo agent
- Broader adapter support
- Streaming replay

Not in v0.1:

- Dashboard
- Hosted service
- Server, Postgres, Redis, or auth
- Broad framework adapter coverage

## Docs

- [Cassette format](docs/cassette-format.md)
- [Replay semantics](docs/replay-semantics.md)
- [Architecture](docs/architecture.md)
