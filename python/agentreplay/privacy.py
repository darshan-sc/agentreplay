"""Payload privacy helpers for cassette recording."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

PrivacyMode = Literal["safe", "hide_all", "transform"]

REDACTED = "[REDACTED]"
HIDDEN = {"value_hidden": True}

_OMIT = object()

_SAFE_ENVELOPE_FIELDS = {
    "schema_version",
    "event",
    "trace_id",
    "span_id",
    "name",
    "provider",
    "model",
    "status",
    "latency_ms",
}

_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "api_token",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "cookies",
    "csrf_token",
    "id_token",
    "password",
    "passwd",
    "private_key",
    "pwd",
    "refresh_token",
    "secret",
    "secret_access_key",
    "secret_key",
    "session_cookie",
    "session_token",
    "set_cookie",
    "token",
}

_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "api_token",
    "authorization",
    "client_secret",
    "cookie",
    "csrf_token",
    "credential",
    "id_token",
    "password",
    "private_key",
    "refresh_token",
    "secret_access_key",
    "secret_key",
    "session_token",
)

_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----.*?-----END (?:[A-Z0-9 ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----",
        re.DOTALL,
    ),
    re.compile(r"\b(?:set-cookie|cookie)\s*:\s*[^,\n]+", re.IGNORECASE),
    re.compile(r"\b[^=\s;]+=[^;\s]+;\s*(?:HttpOnly|Secure|SameSite=[A-Za-z]+)", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9_])[\"']?(?:aws_secret_access_key|secret_access_key|access_key_secret|client_secret|api_key|api_token|access_token|refresh_token|id_token|session_token|csrf_token|token|password|passwd|pwd)[\"']?\s*[:=]\s*(?:[\"'][^\"']*[\"']|[^,\s;&}\]]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA|AIPA|ANPA)[A-Z0-9]{16}\b"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
)


def sanitize_event(
    event: Mapping[str, Any],
    *,
    mode: PrivacyMode = "safe",
    sanitizer: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Return the event payload that is safe to serialize."""

    if mode == "safe":
        return sanitize_payload(event)
    if mode == "transform":
        if sanitizer is None:
            raise ValueError("privacy mode 'transform' requires a sanitizer")
        transformed = sanitizer(event)
        return sanitize_payload(transformed)
    if mode == "hide_all":
        return hide_event_payload(event)
    raise ValueError(f"unknown privacy mode {mode!r}")


def sanitize_payload(value: Any) -> Any:
    """Recursively drop secret keys, redact secret-like strings, and JSON-normalize."""

    payload = _sanitize_value(value)
    if payload is _OMIT:
        return unsupported_payload_marker()
    return payload


def hide_event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    hidden: dict[str, Any] = {}
    event_type = event.get("event")
    for key, value in event.items():
        text_key = str(key)
        if text_key in _SAFE_ENVELOPE_FIELDS:
            safe_value = _sanitize_value(value)
            if safe_value is not _OMIT:
                hidden[text_key] = safe_value

    if event_type == "llm.call":
        hidden.setdefault("input_hash", "hidden:payload")
    elif event_type in {"llm.response", "tool.response"} and "error" in event:
        hidden["error"] = "[HIDDEN]"
    elif event_type in {"llm.response", "tool.response", "agent.step"}:
        hidden["output"] = dict(HIDDEN)
    elif event_type == "retrieval.call":
        hidden["query"] = "[HIDDEN]"
    elif event_type == "retrieval.response":
        hidden["documents"] = []
    elif event_type == "error":
        hidden["message"] = "[HIDDEN]"

    return hidden


def should_drop_key(key: str) -> bool:
    normalized = normalize_key(key)
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized.startswith(("secret_", "token_")):
        return True
    if normalized.endswith(("_password", "_secret", "_token")):
        return True
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def normalize_key(key: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def unsupported_payload_marker() -> dict[str, Any]:
    return {
        "value_unavailable": True,
        "reason": "unsupported_type",
    }


def _sanitize_value(value: Any) -> Any:
    if value is _OMIT:
        return _OMIT
    if value is None:
        return None
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else unsupported_payload_marker()
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_item(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize_value(asdict(value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _sanitize_value(model_dump(mode="json", exclude_none=True))
        except TypeError:
            try:
                return _sanitize_value(model_dump())
            except Exception:
                return unsupported_payload_marker()
        except Exception:
            return unsupported_payload_marker()

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _sanitize_value(to_dict())
        except Exception:
            return unsupported_payload_marker()

    return unsupported_payload_marker()


def _sanitize_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if should_drop_key(key_text) or redact_text(key_text) != key_text:
            continue
        payload = _sanitize_value(item)
        if payload is not _OMIT:
            result[key_text] = payload
    return result


def _sanitize_item(value: Any) -> Any:
    payload = _sanitize_value(value)
    if payload is _OMIT:
        return unsupported_payload_marker()
    return payload
