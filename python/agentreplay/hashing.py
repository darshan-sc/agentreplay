"""Canonical JSON hashing compatible with the Go cassette package."""

from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_PREFIX = "sha256:"

_GO_JSON_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    ord("\u2028"): "\\u2028",
    ord("\u2029"): "\\u2029",
}


def canonical_json(value: Any) -> str:
    """Return compact, key-sorted JSON for a JSON-serializable value."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return encoded.translate(_GO_JSON_ESCAPES)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"canonicalize JSON for hash: {exc}") from exc


def hash_value(value: Any) -> str:
    canonical = canonical_json(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def hash_json(raw: str | bytes | bytearray) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"decode JSON for hash: {exc}") from exc
    return hash_value(value)
