"""OpenAI non-streaming recording hook for AgentReplay cassettes."""

from __future__ import annotations

import functools
import importlib
import json
import math
import os
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .cassette_writer import ALLOWED_EVENTS, SCHEMA_VERSION, CassetteWriter
from .hashing import canonical_json, hash_value

_OMIT = object()
_ACTIVE_CONTEXT = None

_PARAM_FIELDS = {
    "frequency_penalty",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "metadata",
    "presence_penalty",
    "reasoning",
    "response_format",
    "seed",
    "temperature",
    "text",
    "top_logprobs",
    "top_p",
}

_TRANSPORT_FIELDS = {
    "api_key",
    "base_url",
    "client",
    "default_headers",
    "extra_headers",
    "extra_query",
    "headers",
    "http_client",
    "idempotency_key",
    "organization",
    "project",
    "request_options",
    "timeout",
}

_SENSITIVE_EXACT_KEYS = {
    "api_token",
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "credentials",
    "password",
    "passwd",
    "pwd",
    "refresh_token",
    "secret",
    "token",
}

_SENSITIVE_KEY_PARTS = (
    "access_key",
    "access_key_id",
    "api_key",
    "authorization",
    "client_secret",
    "cookie",
    "csrf_token",
    "credential",
    "id_token",
    "password",
    "private_key",
    "secret_access_key",
    "secret_key",
    "session_token",
    "secret",
)

_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\b(?:set-cookie|cookie)\s*:\s*[^,\n]+", re.IGNORECASE),
    re.compile(r"\b[^=\s;]+=[^;\s]+;\s*(?:HttpOnly|Secure|SameSite=[A-Za-z]+)", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9_])[\"']?(?:aws_secret_access_key|secret_access_key|access_key_secret|client_secret|api_key|api_token|token|password|passwd|pwd)[\"']?\s*[:=]\s*(?:[\"'][^\"']*[\"']|[^,\s;&}\]]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA|AIPA|ANPA)[A-Z0-9]{16}\b"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
)


class OpenAIHookError(RuntimeError):
    pass


class OpenAIReplayError(OpenAIHookError):
    pass


class OpenAIReplayDivergenceError(OpenAIReplayError):
    pass


def recording_openai(
    path: str | os.PathLike[str],
    *,
    name: str = "openai",
    metadata: Mapping[str, Any] | None = None,
    patch_target: tuple[type, str] | None = None,
) -> "OpenAIRecorder":
    """Record non-streaming OpenAI Responses API calls into a cassette.

    ``patch_target`` is intentionally available for tests and local SDK-shape
    experiments. Normal callers should rely on the default OpenAI SDK target.
    """

    return OpenAIRecorder(path, name=name, metadata=metadata, patch_target=patch_target)


def replaying_openai(
    path: str | os.PathLike[str],
    *,
    patch_target: tuple[type, str] | None = None,
) -> "OpenAIReplayer":
    """Replay non-streaming OpenAI Responses API calls from a cassette."""

    return OpenAIReplayer(path, patch_target=patch_target)


def record_agent_step(
    name: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    input: Any = _OMIT,
    output: Any = _OMIT,
) -> None:
    """Record an agent.step event when an AgentReplay recorder is active."""

    recorder = _active_recorder()
    if recorder is None:
        return
    recorder._write_agent_step(name, metadata=metadata, input=input, output=output)


def recording_tool(
    name: str,
    *,
    input: Any = _OMIT,
    metadata: Mapping[str, Any] | None = None,
) -> "RecordedToolSpan":
    """Record a tool.call/tool.response span when a recorder is active."""

    return RecordedToolSpan(name, input=input, metadata=metadata)


class OpenAIRecorder:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        name: str,
        metadata: Mapping[str, Any] | None,
        patch_target: tuple[type, str] | None,
    ) -> None:
        self.path = Path(path)
        self.name = name
        self.metadata = metadata or {}
        self.patch_target = patch_target
        self.trace_id = f"tr_{uuid.uuid4().hex}"
        self._span_counter = 0
        self._writer: CassetteWriter | None = None
        self._target_owner: type | None = None
        self._target_method: str | None = None
        self._original_create = None
        self._last_output_hash: str | None = None
        self._trace_ended = False

    def __enter__(self) -> "OpenAIRecorder":
        global _ACTIVE_CONTEXT

        if _ACTIVE_CONTEXT is not None:
            raise OpenAIHookError("OpenAI recording/replay contexts cannot be nested")
        _ACTIVE_CONTEXT = self

        try:
            self._writer = CassetteWriter(self.path).open()
            self._writer.write_event(
                {
                    "event": "trace.start",
                    "trace_id": self.trace_id,
                    "name": self.name,
                    "metadata": self._trace_metadata(),
                }
            )
            self._patch_openai()
            return self
        except Exception:
            self._restore_patch()
            self._close_writer()
            _ACTIVE_CONTEXT = None
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _ACTIVE_CONTEXT

        close_error = None
        try:
            status = "error" if exc_type is not None else "success"
            self._write_trace_end(status)
        except Exception as err:
            close_error = err
        finally:
            self._restore_patch()
            try:
                self._close_writer()
            except Exception as err:
                close_error = close_error or err
            _ACTIVE_CONTEXT = None

        if close_error is not None and exc_type is None:
            raise close_error
        return False

    def _trace_metadata(self) -> dict[str, Any]:
        metadata = {
            "provider": "openai",
            "runtime": "python",
        }
        user_metadata = _to_jsonable(self.metadata)
        if isinstance(user_metadata, dict):
            metadata.update(user_metadata)
        return metadata

    def _patch_openai(self) -> None:
        owner, method = self.patch_target or _resolve_openai_create_target()
        original = getattr(owner, method)

        @functools.wraps(original)
        def wrapper(*args, **kwargs):
            return self._record_create(original, *args, **kwargs)

        setattr(owner, method, wrapper)
        self._target_owner = owner
        self._target_method = method
        self._original_create = original

    def _restore_patch(self) -> None:
        if (
            self._target_owner is not None
            and self._target_method is not None
            and self._original_create is not None
        ):
            setattr(self._target_owner, self._target_method, self._original_create)
        self._target_owner = None
        self._target_method = None
        self._original_create = None

    def _record_create(self, original, *args, **kwargs):
        writer = self._require_writer()
        span_id = self._next_span_id()
        model = _extract_model(args, kwargs)
        request_payload = _request_hash_payload(args, kwargs, model)
        input_hash = hash_value(request_payload)
        params = _request_params(kwargs)

        call_event: dict[str, Any] = {
            "event": "llm.call",
            "trace_id": self.trace_id,
            "span_id": span_id,
            "provider": "openai",
            "model": model,
            "input_hash": input_hash,
        }
        if params:
            call_event["params"] = params
        writer.write_event(call_event)

        started = time.monotonic()
        try:
            response = original(*args, **kwargs)
        except Exception as exc:
            writer.write_event(
                {
                    "event": "llm.response",
                    "trace_id": self.trace_id,
                    "span_id": span_id,
                    "error": _redact_text(f"{exc.__class__.__name__}: {exc}"),
                    "latency_ms": _latency_ms(started),
                }
            )
            raise

        output = _response_output(response)
        output_hash = hash_value(output)
        response_event: dict[str, Any] = {
            "event": "llm.response",
            "trace_id": self.trace_id,
            "span_id": span_id,
            "output": output,
            "output_hash": output_hash,
            "latency_ms": _latency_ms(started),
        }
        usage = _response_usage(response)
        if usage:
            response_event["usage"] = usage
        writer.write_event(response_event)
        self._last_output_hash = output_hash
        return response

    def _write_agent_step(
        self,
        name: str,
        *,
        metadata: Mapping[str, Any] | None,
        input: Any,
        output: Any,
    ) -> None:
        event: dict[str, Any] = {
            "event": "agent.step",
            "trace_id": self.trace_id,
            "name": name,
        }
        _add_jsonable_field(event, "metadata", metadata)
        _add_jsonable_payload(event, "input", input)
        _add_jsonable_payload(event, "output", output)
        self._require_writer().write_event(event)

    def _write_tool_call(
        self,
        span_id: str,
        name: str,
        *,
        input: Any,
        metadata: Mapping[str, Any] | None,
    ) -> None:
        event: dict[str, Any] = {
            "event": "tool.call",
            "trace_id": self.trace_id,
            "span_id": span_id,
            "name": name,
        }
        _add_jsonable_field(event, "metadata", metadata)
        _add_jsonable_payload(event, "input", input)
        self._require_writer().write_event(event)

    def _write_tool_response(
        self,
        span_id: str,
        *,
        output: Any = _OMIT,
        error: str | None = None,
        started: float | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "event": "tool.response",
            "trace_id": self.trace_id,
            "span_id": span_id,
        }
        if error is not None:
            event["error"] = _redact_text(error)
        else:
            _add_jsonable_payload(event, "output", {} if output is _OMIT else output)
        if started is not None:
            event["latency_ms"] = _latency_ms(started)
        self._require_writer().write_event(event)

    def _write_trace_end(self, status: str) -> None:
        if self._trace_ended:
            return
        writer = self._require_writer()
        event: dict[str, Any] = {
            "event": "trace.end",
            "trace_id": self.trace_id,
            "status": status,
        }
        if status == "success" and self._last_output_hash:
            event["output_hash"] = self._last_output_hash
        writer.write_event(event)
        self._trace_ended = True

    def _next_span_id(self) -> str:
        self._span_counter += 1
        return f"sp_{self._span_counter}"

    def _require_writer(self) -> CassetteWriter:
        if self._writer is None:
            raise OpenAIHookError("recording_openai is not active")
        return self._writer

    def _close_writer(self) -> None:
        if self._writer is not None:
            writer = self._writer
            self._writer = None
            writer.close()


class OpenAIReplayer:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        patch_target: tuple[type, str] | None,
    ) -> None:
        self.path = Path(path)
        self.patch_target = patch_target
        self._target_owner: type | None = None
        self._target_method: str | None = None
        self._original_create = None
        self._index: ReplayIndex | None = None

    def __enter__(self) -> "OpenAIReplayer":
        global _ACTIVE_CONTEXT

        if _ACTIVE_CONTEXT is not None:
            raise OpenAIHookError("OpenAI recording/replay contexts cannot be nested")
        _ACTIVE_CONTEXT = self

        try:
            self._index = ReplayIndex.from_path(self.path)
            self._patch_openai()
            return self
        except Exception:
            self._restore_patch()
            _ACTIVE_CONTEXT = None
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _ACTIVE_CONTEXT

        exit_error = None
        try:
            if exc_type is None and self._index is not None:
                self._index.assert_exhausted()
        except Exception as err:
            exit_error = err
        finally:
            self._restore_patch()
            _ACTIVE_CONTEXT = None

        if exit_error is not None:
            raise exit_error
        return False

    def _patch_openai(self) -> None:
        owner, method = self.patch_target or _resolve_openai_create_target()
        original = getattr(owner, method)

        @functools.wraps(original)
        def wrapper(*args, **kwargs):
            return self._replay_create(*args, **kwargs)

        setattr(owner, method, wrapper)
        self._target_owner = owner
        self._target_method = method
        self._original_create = original

    def _restore_patch(self) -> None:
        if (
            self._target_owner is not None
            and self._target_method is not None
            and self._original_create is not None
        ):
            setattr(self._target_owner, self._target_method, self._original_create)
        self._target_owner = None
        self._target_method = None
        self._original_create = None

    def _replay_create(self, *args, **kwargs) -> "RecordedOpenAIResponse":
        if self._index is None:
            raise OpenAIReplayError("replaying_openai is not active")

        model = _extract_model(args, kwargs)
        request = LLMReplayRequest(
            provider="openai",
            model=model,
            input_hash=hash_value(_request_hash_payload(args, kwargs, model)),
            params=_request_params(kwargs),
        )
        exchange = self._index.match_llm(request)
        return RecordedOpenAIResponse(exchange.call, exchange.response)


class RecordedToolSpan:
    def __init__(
        self,
        name: str,
        *,
        input: Any,
        metadata: Mapping[str, Any] | None,
    ) -> None:
        self.name = name
        self.input = input
        self.metadata = metadata
        self.span_id: str | None = None
        self._recorder: OpenAIRecorder | None = None
        self._started: float | None = None
        self._output: Any = _OMIT
        self._closed = False

    def __enter__(self) -> "RecordedToolSpan":
        recorder = _active_recorder()
        if recorder is None:
            return self

        self._recorder = recorder
        self.span_id = recorder._next_span_id()
        self._started = time.monotonic()
        recorder._write_tool_call(
            self.span_id,
            self.name,
            input=self.input,
            metadata=self.metadata,
        )
        return self

    def set_output(self, output: Any) -> None:
        self._output = output

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._closed or self._recorder is None or self.span_id is None:
            return False

        self._closed = True
        if exc_type is not None:
            message = f"{exc_type.__name__}: {exc}" if exc is not None else exc_type.__name__
            self._recorder._write_tool_response(
                self.span_id,
                error=message,
                started=self._started,
            )
            return False

        self._recorder._write_tool_response(
            self.span_id,
            output=self._output,
            started=self._started,
        )
        return False


class LLMReplayRequest:
    def __init__(self, *, provider: str, model: str, input_hash: str, params: dict[str, Any]) -> None:
        self.provider = provider
        self.model = model
        self.input_hash = input_hash
        self.params = params


class LLMReplayExchange:
    def __init__(self, call: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        self.call = call
        self.response = response


class LLMReplayState:
    def __init__(self, call: Mapping[str, Any]) -> None:
        self.call = call
        self.response: Mapping[str, Any] | None = None


class ReplayIndex:
    def __init__(self, exchanges: list[LLMReplayExchange]) -> None:
        self._exchanges = exchanges
        self._next_llm = 0

    @classmethod
    def from_path(cls, path: Path) -> "ReplayIndex":
        events = _read_cassette_events(path)
        _validate_replay_cassette_events(path, events)
        return cls(_build_llm_exchanges(events))

    def match_llm(self, request: LLMReplayRequest) -> LLMReplayExchange:
        if self._next_llm >= len(self._exchanges):
            raise OpenAIReplayDivergenceError("replay exhausted: no recorded llm exchange remains")

        exchange = self._exchanges[self._next_llm]
        _validate_recorded_call(self._next_llm + 1, exchange.call)
        _match_llm_request(self._next_llm + 1, exchange.call, request)
        _validate_recorded_response(self._next_llm + 1, exchange.response)
        self._next_llm += 1
        return exchange

    def assert_exhausted(self) -> None:
        remaining = len(self._exchanges) - self._next_llm
        if remaining > 0:
            raise OpenAIReplayDivergenceError(
                f"replay incomplete: {remaining} recorded llm exchange(s) were not consumed"
            )

    @property
    def llm_exchange_count(self) -> int:
        return len(self._exchanges)

    def assert_replayable(self) -> None:
        for index, exchange in enumerate(self._exchanges, start=1):
            _validate_recorded_call(index, exchange.call)
            _validate_recorded_response(index, exchange.response)


class RecordedOpenAIResponse:
    """Small response shim returned by replaying_openai."""

    def __init__(self, call: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        self.call = dict(call)
        self.event = dict(response)
        self.model = call.get("model")
        self.output = response.get("output")
        self.output_text = _output_text_from_recorded(self.output)
        self.usage = _usage_namespace(response.get("usage"))

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "output": self.output,
            "output_text": self.output_text,
        }
        if self.usage is not None:
            payload["usage"] = vars(self.usage)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _read_cassette_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OpenAIReplayError(f"read cassette {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            raise OpenAIReplayError(f"{path}:{line_number}: blank lines are not valid cassette events")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OpenAIReplayError(f"{path}:{line_number}: invalid JSON event: {exc}") from exc
        if not isinstance(event, dict):
            raise OpenAIReplayError(f"{path}:{line_number}: cassette event must be an object")
        event["_line"] = line_number
        events.append(event)
    return events


def _validate_replay_cassette_events(path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        raise OpenAIReplayError(f"{path}: cassette must contain at least one event")

    saw_trace_start = False
    saw_trace_end = False

    for index, event in enumerate(events):
        line = event.get("_line")
        schema_version = event.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise OpenAIReplayError(f"{path}:{line}: unsupported schema_version {schema_version!r}")

        event_type = event.get("event")
        if not isinstance(event_type, str) or not event_type:
            raise OpenAIReplayError(f"{path}:{line}: event must be a non-empty string")
        if event_type not in ALLOWED_EVENTS:
            raise OpenAIReplayError(f"{path}:{line}: unknown event type {event_type!r}")

        if index == 0 and event_type != "trace.start":
            raise OpenAIReplayError(f"{path}:{line}: cassette must start with trace.start")
        if event_type == "trace.start":
            if saw_trace_start:
                raise OpenAIReplayError(f"{path}:{line}: cassette contains more than one trace.start event")
            saw_trace_start = True
        if saw_trace_end:
            raise OpenAIReplayError(f"{path}:{line}: events cannot appear after trace.end")
        if event_type == "trace.end":
            saw_trace_end = True

    if not saw_trace_end:
        raise OpenAIReplayError(f"{path}: cassette must end with trace.end")


def _build_llm_exchanges(events: list[dict[str, Any]]) -> list[LLMReplayExchange]:
    states: list[LLMReplayState] = []
    active: dict[str, int] = {}

    for event in events:
        event_type = event.get("event")
        if event_type == "llm.call":
            span_id = event.get("span_id")
            if not isinstance(span_id, str) or not span_id:
                raise OpenAIReplayError(f"llm.call at line {event.get('_line')} is missing span_id")
            if span_id in active:
                first_line = states[active[span_id]].call.get("_line")
                raise OpenAIReplayError(
                    f'llm.call span_id "{span_id}" at line {event.get("_line")} is already active from line {first_line}'
                )
            active[span_id] = len(states)
            states.append(LLMReplayState(event))
        elif event_type == "llm.response":
            span_id = event.get("span_id")
            if not isinstance(span_id, str) or not span_id:
                raise OpenAIReplayError(f"llm.response at line {event.get('_line')} is missing span_id")
            if span_id not in active:
                raise OpenAIReplayError(
                    f'llm.response span_id "{span_id}" at line {event.get("_line")} has no prior llm.call'
                )
            states[active[span_id]].response = event
            del active[span_id]

    if active:
        span_id, state_index = next(iter(active.items()))
        call = states[state_index].call
        raise OpenAIReplayError(
            f'llm.call span_id "{span_id}" at line {call.get("_line")} is missing llm.response'
        )

    exchanges: list[LLMReplayExchange] = []
    for state in states:
        if state.response is None:
            raise OpenAIReplayError(
                f'llm.call span_id "{state.call.get("span_id")}" at line {state.call.get("_line")} is missing llm.response'
            )
        exchanges.append(LLMReplayExchange(call=state.call, response=state.response))
    return exchanges


def _match_llm_request(index: int, call: Mapping[str, Any], request: LLMReplayRequest) -> None:
    recorded_provider = call.get("provider")
    if recorded_provider != request.provider:
        raise OpenAIReplayDivergenceError(
            f'llm replay mismatch at exchange {index}: provider mismatch: recorded {recorded_provider!r}, got {request.provider!r}'
        )

    recorded_model = call.get("model")
    if recorded_model != request.model:
        raise OpenAIReplayDivergenceError(
            f'llm replay mismatch at exchange {index}: model mismatch: recorded {recorded_model!r}, got {request.model!r}'
        )

    recorded_input_hash = call.get("input_hash")
    if recorded_input_hash != request.input_hash:
        raise OpenAIReplayDivergenceError(
            f'llm replay mismatch at exchange {index}: input_hash mismatch: recorded {recorded_input_hash!r}, got {request.input_hash!r}'
        )

    recorded_params = call.get("params", {})
    if recorded_params != request.params:
        raise OpenAIReplayDivergenceError(
            "llm replay mismatch at exchange "
            f"{index}: params mismatch: recorded {_describe_params(recorded_params)}, got {_describe_params(request.params)}"
        )


def _validate_recorded_call(index: int, call: Mapping[str, Any]) -> None:
    for field in ("provider", "model", "input_hash"):
        value = call.get(field)
        if not isinstance(value, str) or not value:
            raise OpenAIReplayError(
                f"recorded llm call at exchange {index} is missing replayable {field}"
            )

    if "params" in call and not isinstance(call["params"], Mapping):
        raise OpenAIReplayError(
            f"recorded llm call at exchange {index} has non-object params"
        )


def _validate_recorded_response(index: int, response: Mapping[str, Any]) -> None:
    if "error" in response:
        raise OpenAIReplayError(f"recorded llm response at exchange {index} contains error: {response['error']}")
    if "output" not in response:
        raise OpenAIReplayError(f"recorded llm response at exchange {index} is missing replayable output")
    if response["output"] is None:
        raise OpenAIReplayError(f"recorded llm response at exchange {index} has null output")


def _describe_params(value: Any) -> str:
    try:
        return canonical_json(value)
    except ValueError:
        return repr(value)


def _active_recorder() -> OpenAIRecorder | None:
    if isinstance(_ACTIVE_CONTEXT, OpenAIRecorder):
        return _ACTIVE_CONTEXT
    return None


def _add_jsonable_field(event: dict[str, Any], field: str, value: Any) -> None:
    if value is _OMIT or value is None:
        return
    payload = _recorded_payload_value(value)
    if payload is not _OMIT:
        event[field] = payload


def _add_jsonable_payload(event: dict[str, Any], field: str, value: Any) -> None:
    if value is _OMIT:
        return
    payload = _normalize_recorded_payload(field, _recorded_payload_value(value))
    event[field] = payload
    event[f"{field}_hash"] = hash_value(payload)


def _normalize_recorded_payload(field: str, value: Any) -> Any:
    if value is None:
        return {"value": None}
    if field == "output" and value == "":
        return {"value": ""}
    return value


def _recorded_payload_value(value: Any) -> Any:
    if value is _OMIT:
        return _OMIT
    if value is None:
        return None
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _unsupported_payload_marker()
    if isinstance(value, Mapping):
        return _recorded_payload_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_recorded_payload_item(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _recorded_payload_value(asdict(value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _recorded_payload_value(model_dump(mode="json", exclude_none=True))
        except TypeError:
            try:
                return _recorded_payload_value(model_dump())
            except Exception:
                return _unsupported_payload_marker()
        except Exception:
            return _unsupported_payload_marker()

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _recorded_payload_value(to_dict())
        except Exception:
            return _unsupported_payload_marker()

    return _unsupported_payload_marker()


def _recorded_payload_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if _drop_key(key_text) or _redact_text(key_text) != key_text:
            continue
        result[key_text] = _recorded_payload_item(item)
    return result


def _recorded_payload_item(value: Any) -> Any:
    payload = _recorded_payload_value(value)
    if payload is _OMIT:
        return _unsupported_payload_marker()
    return payload


def _unsupported_payload_marker() -> dict[str, Any]:
    return {
        "value_unavailable": True,
        "reason": "unsupported_type",
    }


def _output_text_from_recorded(output: Any) -> str | None:
    if isinstance(output, Mapping):
        text = output.get("text")
        if isinstance(text, str):
            return text
    if isinstance(output, str):
        return output
    return None


def _usage_namespace(value: Any) -> SimpleNamespace | None:
    if not isinstance(value, Mapping):
        return None
    return SimpleNamespace(**{str(key): item for key, item in value.items()})


def _resolve_openai_create_target() -> tuple[type, str]:
    candidates = (
        ("openai.resources.responses.responses", "Responses"),
        ("openai.resources.responses", "Responses"),
    )
    errors: list[str] = []
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
            owner = getattr(module, class_name)
            getattr(owner, "create")
            return owner, "create"
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module_name}.{class_name}: {exc}")
    raise OpenAIHookError(
        "could not find OpenAI Responses.create patch target; "
        "install a supported openai package or pass patch_target. "
        + "; ".join(errors)
    )


def _extract_model(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> str:
    model = kwargs.get("model")
    if isinstance(model, str) and model:
        return model
    if len(args) > 1 and isinstance(args[1], str) and args[1]:
        return args[1]
    return "unknown"


def _request_hash_payload(args: tuple[Any, ...], kwargs: Mapping[str, Any], model: str) -> dict[str, Any]:
    payload = _sanitize_mapping(kwargs)
    payload["model"] = model

    positional = args[1:]
    if positional:
        jsonable_positional = _to_jsonable(list(positional))
        if jsonable_positional is not _OMIT:
            payload["positional_args"] = jsonable_positional
    return payload


def _request_params(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key not in _PARAM_FIELDS or _drop_key(key):
            continue
        jsonable = _to_jsonable(value)
        if jsonable is not _OMIT:
            params[key] = jsonable
    return params


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        text_key = str(key)
        if _drop_key(text_key):
            continue
        jsonable = _to_jsonable(item)
        if jsonable is not _OMIT:
            sanitized[text_key] = jsonable
    return sanitized


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            jsonable = _to_jsonable(item)
            if jsonable is not _OMIT:
                result.append(jsonable)
        return result
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_jsonable(model_dump(mode="json", exclude_none=True))
        except TypeError:
            try:
                return _to_jsonable(model_dump())
            except Exception:
                return _OMIT
        except Exception:
            return _OMIT

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _to_jsonable(to_dict())
        except Exception:
            return _OMIT

    return _OMIT


def _drop_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _TRANSPORT_FIELDS:
        return True
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized.endswith(("_password", "_secret", "_token")):
        return True
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _normalize_key(key: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _response_output(response: Any) -> dict[str, Any]:
    text = _extract_response_text(response)
    if text:
        return {"text": text}

    raw = _compact_response_raw(response)
    if raw is not _OMIT and raw not in ({}, []):
        return {"raw": raw}
    return {"raw": repr(response)}


def _extract_response_text(response: Any) -> str | None:
    output_text = _read_value(response, "output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    text = _read_value(response, "text")
    if isinstance(text, str) and text:
        return text

    output = _read_value(response, "output")
    fragments = _collect_output_text(output)
    if fragments:
        return "".join(fragments)
    return None


def _collect_output_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str) and text:
            return [text]
        fragments: list[str] = []
        for key in ("content", "output"):
            fragments.extend(_collect_output_text(value.get(key)))
        return fragments
    if isinstance(value, (list, tuple)):
        fragments = []
        for item in value:
            fragments.extend(_collect_output_text(item))
        return fragments

    content = _read_value(value, "content")
    if content is not None:
        return _collect_output_text(content)
    text = _read_value(value, "text")
    if isinstance(text, str) and text:
        return [text]
    return []


def _compact_response_raw(response: Any) -> Any:
    raw = _to_jsonable(response)
    if isinstance(raw, Mapping):
        compact = {
            key: value
            for key, value in raw.items()
            if key in {"id", "model", "output", "status", "type"}
        }
        return compact or raw
    return raw


def _response_usage(response: Any) -> dict[str, int]:
    usage = _read_value(response, "usage")
    if usage is None:
        return {}

    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = _read_value(usage, key)
        if isinstance(value, int):
            result[key] = value
    return result


def _read_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    try:
        return getattr(value, key)
    except Exception:
        return None


def _latency_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
