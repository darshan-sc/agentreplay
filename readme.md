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
- Sanitizes cassette payloads before writing and hashes sanitized payloads.
- Validates cassette structure, event order, spans, and trace consistency.
- Diffs two cassettes to expose replay-relevant changes.
- Generates pytest regression files from one or more cassettes.
- Records Python agent step and tool span events alongside LLM calls.
- Provides a Go CLI for `record`, `replay`, `validate`, `inspect`, `diff`, and `generate-tests`.
- Provides Python context managers for direct OpenAI record/replay hooks.

## Install

Requirements:

- Go 1.20 or newer
- Python 3.11 or newer

Set up a local development environment:

```bash
make setup
```

This creates `.venv`, installs the Python package in editable mode, and installs the development test dependency for generated pytest files.

If you want the LangGraph demo to use real LangGraph instead of the built-in local fallback, run:

```bash
make setup-all
```

To use the Python package from another project:

```bash
python3 -m pip install -e ./python
```

Build a local CLI binary:

```bash
make build-cli
bin/agentreplay validate traces/sample.replay.jsonl
```

## Quickstart

Run the test suite and inspect the sample cassette:

```bash
make test

make validate-sample
make inspect-sample
make generate-sample-tests
```

Expected validation output:

```text
OK: traces/sample.replay.jsonl (4 events)
```

## Record and replay an OpenAI call

Put your OpenAI key in `.env.local`. You can start from `.env.example`:

```bash
cp .env.example .env.local
OPENAI_API_KEY=...
```

Record one live call:

```bash
make smoke-record
```

Replay the same workflow offline:

```bash
make smoke-replay
```

During replay, AgentReplay patches the OpenAI Responses API inside the process and returns the recorded response from the cassette. If the code asks for a different request, changes order, or cannot match the recorded call, replay fails instead of calling the live API.

## Run the LangGraph-style demo

Record a small tool-plus-LLM agent run:

```bash
make langgraph-record
```

Replay it offline:

```bash
make langgraph-replay
```

The demo uses LangGraph's `StateGraph` when LangGraph is installed, and otherwise runs the same two-node flow locally. The cassette includes `agent.step`, `tool.call`, `tool.response`, `llm.call`, and `llm.response` events.

## Generate pytest regression tests

Generate a pytest file from one or more cassettes:

```bash
go run ./cmd/agentreplay generate-tests traces/sample.replay.jsonl --framework pytest --out tests/test_agent_replays.py
```

The generated tests call `agentreplay.pytest.replay_case(...)`, which loads each cassette and checks that its recorded LLM exchanges are usable for offline replay. With `pytest` installed, run the generated file like any other pytest test module.

After `make setup`, you can also run:

```bash
make test-pytest
```

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

Record agent and tool events in the same cassette:

```python
from agentreplay import record_agent_step, recording_openai, recording_tool

with recording_openai("traces/run.replay.jsonl", name="agent"):
    record_agent_step("lookup", input={"query": "refund policy"})
    with recording_tool("search_docs", input={"query": "refund policy"}) as tool:
        tool.set_output({"result": "Refunds require the original order number."})
```

## Payload privacy

The default recorder privacy mode is `safe`: AgentReplay recursively drops common secret-bearing keys, redacts common secret-like strings, writes the sanitized payload, and recalculates hashes from the sanitized payload.

For highly sensitive prompts or tool outputs, use `hide_all`:

```python
with recording_openai("traces/private.replay.jsonl", privacy="hide_all"):
    ...
```

For domain-specific private data, use `transform` with your own sanitizer:

```python
with recording_openai("traces/run.replay.jsonl", privacy="transform", sanitizer=my_sanitizer):
    ...
```

See [Payload privacy](docs/payload-privacy.md) for the exact behavior and tradeoffs.

## CLI

```text
agentreplay records, replays, diffs, and tests LLM-agent runs.

Usage:
  agentreplay validate <cassette.replay.jsonl>
  agentreplay inspect <cassette.replay.jsonl>
  agentreplay record --out <cassette.replay.jsonl> -- <command> [args...]
  agentreplay replay <cassette.replay.jsonl> -- <command> [args...]
  agentreplay diff <before.replay.jsonl> <after.replay.jsonl>
  agentreplay generate-tests <cassette...> --framework pytest --out <file>
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
- `agentreplay generate-tests`
- Versioned JSONL cassette reader and writer
- Cassette validator with trace/span consistency checks
- Deterministic JSON hash helpers
- In-process LLM replay index and request matching
- Python OpenAI non-streaming recording and replay hooks
- Python agent step and tool span recording helpers
- Payload privacy modes: `safe`, `hide_all`, and `transform`
- Pytest regression test generation
- LangGraph-style demo agent
- Synthetic sample cassette

Planned next:

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
- [Payload privacy](docs/payload-privacy.md)
- [Architecture](docs/architecture.md)
