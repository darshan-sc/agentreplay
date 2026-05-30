from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentreplay.cassette_writer import CassetteWriter
from agentreplay.hashing import hash_value
from agentreplay.privacy import sanitize_payload


SECRET_KEY_VARIANTS = [
    "api_key",
    "api-key",
    "apiKey",
    "ApiKey",
    "APIKey",
    "access_key",
    "accessKey",
    "private_key",
    "privateKey",
    "client_secret",
    "secret_access_key",
    "AWS_SECRET_ACCESS_KEY",
    "secret_key",
    "session_token",
    "csrf_token",
    "CSRFToken",
    "id_token",
    "IDToken",
    "password",
    "passwd",
    "pwd",
    "cookie",
    "cookies",
    "set-cookie",
    "session_cookie",
    "authorization",
    "bearer",
    "api_token",
    "refresh_token",
    "token",
    "secret",
    "tokenValue",
    "secretValue",
]

BENIGN_KEY_VARIANTS = [
    "max_output_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "temperature",
]

SECRET_VALUE_CASES = [
    ("openai", "sk-contractsecret123456"),
    ("bearer", "Bearer contract-token.123"),
    ("credential_url", "postgres://user:pass@db/app"),
    ("env_assignment", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("json_assignment", '{"password":"hunter2","safe":"ok"}'),
    ("cookie_header", "Cookie: sid=abc123; HttpOnly"),
    (
        "private_key",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----",
    ),
    ("aws_access_key_id", "AKIAIOSFODNN7EXAMPLE"),
]


def mask_private_value(value):
    if isinstance(value, dict):
        return {
            key: mask_private_value("[PRIVATE]" if item == "private-value" else item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_private_value(item) for item in value]
    return value


class PrivacyContractTests(unittest.TestCase):
    def test_safe_mode_key_corpus_drops_secrets_and_keeps_benign_near_misses(self) -> None:
        payload = {key: f"drop-{index}" for index, key in enumerate(SECRET_KEY_VARIANTS)}
        payload.update({key: index for index, key in enumerate(BENIGN_KEY_VARIANTS)})

        sanitized = sanitize_payload(payload)

        for key in SECRET_KEY_VARIANTS:
            self.assertNotIn(key, sanitized)
        for index, key in enumerate(BENIGN_KEY_VARIANTS):
            self.assertEqual(sanitized[key], index)

    def test_safe_mode_value_corpus_redacts_nested_secret_values(self) -> None:
        payload = {
            "cases": {name: value for name, value in SECRET_VALUE_CASES},
            "safe": "ok",
        }

        sanitized = sanitize_payload(payload)
        raw = json.dumps(sanitized, sort_keys=True, separators=(",", ":"))

        self.assertEqual(sanitized["safe"], "ok")
        for _, value in SECRET_VALUE_CASES:
            self.assertNotIn(value, raw)
        self.assertNotIn("hunter2", raw)
        self.assertNotIn("user:pass", raw)
        self.assertNotIn("OPENSSH PRIVATE KEY", raw)
        self.assertIn("[REDACTED]", raw)

    def test_writer_hashes_sanitized_payload_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "hash-contract.replay.jsonl"

            with CassetteWriter(cassette) as writer:
                event = writer.write_event(
                    {
                        "event": "tool.response",
                        "trace_id": "tr_contract",
                        "span_id": "sp_1",
                        "output": {
                            "safe": "ok",
                            "token": "drop-token",
                            "message": "Bearer contract-token.123",
                        },
                    }
                )

            self.assertEqual(event["output"], {"safe": "ok", "message": "[REDACTED]"})
            self.assertEqual(event["output_hash"], hash_value(event["output"]))
            raw = cassette.read_text(encoding="utf-8")
            self.assertNotIn("drop-token", raw)
            self.assertNotIn("contract-token", raw)

    def test_hide_all_mode_contract_preserves_structure_without_content_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "hide-contract.replay.jsonl"

            with CassetteWriter(cassette, privacy="hide_all") as writer:
                writer.write_event(
                    {
                        "event": "llm.call",
                        "trace_id": "tr_contract",
                        "span_id": "sp_1",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "input_hash": hash_value({"prompt": "secret"}),
                        "params": {"temperature": 0},
                    }
                )
                response = writer.write_event(
                    {
                        "event": "llm.response",
                        "trace_id": "tr_contract",
                        "span_id": "sp_1",
                        "output": {"text": "secret output"},
                    }
                )

            events = [
                json.loads(line)
                for line in cassette.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[0]["input_hash"], "hidden:payload")
            self.assertNotIn("params", events[0])
            self.assertEqual(response["output"], {"value_hidden": True})
            self.assertNotIn("output_hash", response)
            self.assertNotIn("secret", cassette.read_text(encoding="utf-8"))

    def test_hide_all_mode_contract_preserves_response_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "hide-error-contract.replay.jsonl"

            with CassetteWriter(cassette, privacy="hide_all") as writer:
                event = writer.write_event(
                    {
                        "event": "llm.response",
                        "trace_id": "tr_contract",
                        "span_id": "sp_1",
                        "error": "RuntimeError: secret failure",
                    }
                )

            self.assertEqual(event["error"], "[HIDDEN]")
            self.assertNotIn("output", event)
            self.assertNotIn("secret failure", cassette.read_text(encoding="utf-8"))

    def test_transform_mode_contract_hashes_transformed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "transform-contract.replay.jsonl"

            with CassetteWriter(cassette, privacy="transform", sanitizer=mask_private_value) as writer:
                event = writer.write_event(
                    {
                        "event": "tool.response",
                        "trace_id": "tr_contract",
                        "span_id": "sp_1",
                        "output": {"safe": "private-value"},
                    }
                )

            self.assertEqual(event["output"], {"safe": "[PRIVATE]"})
            self.assertEqual(event["output_hash"], hash_value({"safe": "[PRIVATE]"}))
            self.assertNotIn("private-value", cassette.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
