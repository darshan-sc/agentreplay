"""OpenAI non-streaming recording hook for AgentReplay cassettes."""

from __future__ import annotations

import functools
import importlib
import math
import os
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .cassette_writer import CassetteWriter
from .hashing import hash_value

_OMIT = object()
_ACTIVE_RECORDER = None

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
    "extra_body",
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
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
}

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
)

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
)


class OpenAIHookError(RuntimeError):
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
        global _ACTIVE_RECORDER

        if _ACTIVE_RECORDER is not None:
            raise OpenAIHookError("recording_openai contexts cannot be nested")
        _ACTIVE_RECORDER = self

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
            _ACTIVE_RECORDER = None
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _ACTIVE_RECORDER

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
            _ACTIVE_RECORDER = None

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
    normalized = key.lower().replace("-", "_")
    if normalized in _TRANSPORT_FIELDS:
        return True
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized.endswith(("_password", "_secret", "_token")):
        return True
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


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
