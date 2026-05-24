# Replay Semantics

Replay should freeze a previous agent run and make divergence obvious.

## Default Rule

When replaying, AgentReplay should match each live request attempt against the cassette. If the normalized provider, model, params, and input hash match, it returns the recorded response.

## Divergence

Replay must fail loudly when:

- The workflow asks for an LLM call that is not present in the cassette.
- The call order changes.
- The normalized input hash changes.
- A recorded response is missing or malformed.

Silent live fallback is not allowed by default. A future `--allow-live-fallback` flag can exist, but it must be explicit.

## Limits

- Replay proves that a previous behavior can be frozen. It does not prove a future live model will behave the same.
- Streaming, external APIs, and hidden state require separate event support.
- Intentional prompt or model changes may diverge. The tool should make that divergence inspectable.
