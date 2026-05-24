# Architecture

## v0.1

```text
User's LangGraph / OpenAI-based agent
        |
        | Python hook captures LLM calls and tool events
        v
agentreplay recorder
        |
        | writes versioned cassette
        v
trace.replay.jsonl
        |
        +--> agentreplay replay
        +--> agentreplay diff
        +--> agentreplay generate-tests
```

## Boundaries

- Go owns cassette reading, validation, diffing, replay matching, CLI orchestration, and code generation.
- Python owns thin runtime hooks for OpenAI and examples that integrate with agent frameworks.
- The cassette format remains framework-neutral.

## Deferred

These are outside v0.1:

- OTLP daemon.
- Postgres trace index.
- Redis.
- Dashboard.
- Hosted service.
- Broad framework adapter support.
