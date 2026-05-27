"""Minimal JSONL cassette writer for Python runtime hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "0.1"

ALLOWED_EVENTS = {
    "trace.start",
    "llm.call",
    "llm.response",
    "tool.call",
    "tool.response",
    "retrieval.call",
    "retrieval.response",
    "agent.step",
    "error",
    "trace.end",
}


class CassetteWriter:
    """Write compact, flushed JSONL cassette events with a stable schema."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._file = None

    def __enter__(self) -> "CassetteWriter":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> "CassetteWriter":
        if self._file is not None:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        self._file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        return self

    def write_event(self, fields: Mapping[str, Any]) -> None:
        if self._file is None:
            raise RuntimeError("cassette writer is not open")

        event = dict(fields)
        event.setdefault("schema_version", SCHEMA_VERSION)
        _validate_event(event)

        json.dump(
            event,
            self._file,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        self._file.write("\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is None:
            return
        file_obj = self._file
        self._file = None
        file_obj.close()


def _validate_event(event: Mapping[str, Any]) -> None:
    version = event.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}")

    event_type = event.get("event")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event must be a non-empty string")
    if event_type not in ALLOWED_EVENTS:
        raise ValueError(f"unknown event type {event_type!r}")

    if event_type == "trace.start":
        _require_string(event, "trace_id")
        _require_string(event, "name")
    elif event_type == "llm.call":
        _require_string(event, "span_id")
        _require_string(event, "provider")
        _require_string(event, "model")
        _require_string(event, "input_hash")
    elif event_type == "llm.response":
        _require_string(event, "span_id")
        _require_any(event, ("output", "output_hash", "error"))
    elif event_type == "tool.call":
        _require_string(event, "span_id")
        _require_string(event, "name")
    elif event_type == "tool.response":
        _require_string(event, "span_id")
        _require_any(event, ("output", "error"))
    elif event_type == "retrieval.call":
        _require_string(event, "span_id")
        _require_any(event, ("query", "input_hash"))
    elif event_type == "retrieval.response":
        _require_string(event, "span_id")
        _require_any(event, ("documents", "output_hash"))
    elif event_type == "agent.step":
        _require_string(event, "name")
    elif event_type == "error":
        _require_string(event, "message")
    elif event_type == "trace.end":
        _require_string(event, "trace_id")
        _require_string(event, "status")


def _require_string(event: Mapping[str, Any], field: str) -> None:
    value = event.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")


def _require_any(event: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        if field not in event:
            continue
        value = event[field]
        if field == "documents":
            if isinstance(value, list):
                return
            raise ValueError("documents must be an array")
        if value is None:
            raise ValueError(f"{field} must not be null")
        if isinstance(value, str) and not value:
            raise ValueError(f"{field} must not be empty")
        return
    raise ValueError(f"missing one of: {fields}")
