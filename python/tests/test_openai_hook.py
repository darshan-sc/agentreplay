from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentreplay.hashing import hash_value
from agentreplay.openai_hook import (
    OpenAIReplayDivergenceError,
    OpenAIReplayError,
    record_agent_step,
    recording_openai,
    recording_tool,
    replaying_openai,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeUsage:
    input_tokens = 3
    output_tokens = 2
    total_tokens = 5


class FakeResponse:
    output_text = "Hello from fake OpenAI."
    usage = FakeUsage()


class FakeResponses:
    def create(self, **kwargs):
        return FakeResponse()


class FailingResponses:
    def create(self, **kwargs):
        raise RuntimeError("boom sk-testsecret123456")


class OfflineResponses:
    def create(self, **kwargs):
        raise AssertionError("live OpenAI method should not be called during replay")


class SecretRepr:
    def __repr__(self) -> str:
        return "SecretRepr(sk-reprsecret123456)"


class OpenAIHookTests(unittest.TestCase):
    def test_successful_responses_create_writes_valid_cassette(self) -> None:
        original = FakeResponses.create

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "run.replay.jsonl"
            fake = FakeResponses()

            with recording_openai(
                cassette,
                name="unit",
                metadata={"prompt_version": "test-v1", "api_token": "drop-me"},
                patch_target=(FakeResponses, "create"),
            ):
                self.assertIsNot(FakeResponses.create, original)
                response = fake.create(
                    model="gpt-4.1-mini",
                    input="Say hello",
                    temperature=0,
                    max_output_tokens=16,
                    extra_body={"parallel_tool_calls": False},
                    extra_headers={"Authorization": "Bearer secret"},
                    api_key="sk-secretvalue123456",
                )

            self.assertIs(FakeResponses.create, original)
            self.assertIsInstance(response, FakeResponse)

            events = _read_events(cassette)
            self.assertEqual(
                [event["event"] for event in events],
                ["trace.start", "llm.call", "llm.response", "trace.end"],
            )
            self.assertEqual(events[0]["metadata"]["runtime"], "python")
            self.assertEqual(events[0]["metadata"]["provider"], "openai")
            self.assertEqual(events[0]["metadata"]["prompt_version"], "test-v1")

            call = events[1]
            self.assertEqual(call["provider"], "openai")
            self.assertEqual(call["model"], "gpt-4.1-mini")
            self.assertEqual(call["params"], {"max_output_tokens": 16, "temperature": 0})
            self.assertEqual(
                call["input_hash"],
                hash_value(
                    {
                        "model": "gpt-4.1-mini",
                        "input": "Say hello",
                        "extra_body": {"parallel_tool_calls": False},
                        "max_output_tokens": 16,
                        "temperature": 0,
                    }
                ),
            )

            llm_response = events[2]
            self.assertEqual(llm_response["output"], {"text": "Hello from fake OpenAI."})
            self.assertEqual(llm_response["usage"]["input_tokens"], 3)
            self.assertEqual(llm_response["usage"]["output_tokens"], 2)
            self.assertEqual(events[3]["status"], "success")
            self.assertEqual(events[3]["output_hash"], llm_response["output_hash"])

            raw = cassette.read_text(encoding="utf-8")
            self.assertNotIn("Authorization", raw)
            self.assertNotIn("Bearer secret", raw)
            self.assertNotIn("sk-secretvalue", raw)
            self.assertNotIn("drop-me", raw)

            _validate_with_go(cassette)

    def test_replaying_responses_create_returns_recorded_response_offline(self) -> None:
        original = OfflineResponses.create

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _record_success_cassette(Path(tempdir))
            offline = OfflineResponses()

            with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                self.assertIsNot(OfflineResponses.create, original)
                response = offline.create(
                    model="gpt-4.1-mini",
                    input="Say hello",
                    temperature=0,
                    max_output_tokens=16,
                )

            self.assertIs(OfflineResponses.create, original)
            self.assertEqual(response.output_text, "Hello from fake OpenAI.")
            self.assertEqual(response.output, {"text": "Hello from fake OpenAI."})
            self.assertEqual(response.usage.input_tokens, 3)
            self.assertEqual(response.usage.output_tokens, 2)

    def test_replay_divergence_does_not_consume_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _record_success_cassette(Path(tempdir))
            offline = OfflineResponses()

            with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                with self.assertRaisesRegex(OpenAIReplayDivergenceError, "input_hash mismatch"):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Changed prompt",
                        temperature=0,
                        max_output_tokens=16,
                    )

                response = offline.create(
                    model="gpt-4.1-mini",
                    input="Say hello",
                    temperature=0,
                    max_output_tokens=16,
                )

            self.assertEqual(response.output_text, "Hello from fake OpenAI.")

    def test_replay_rejects_generation_option_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _record_success_cassette(Path(tempdir))
            offline = OfflineResponses()

            with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                with self.assertRaisesRegex(OpenAIReplayDivergenceError, "input_hash mismatch"):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Say hello",
                        temperature=1,
                        max_output_tokens=16,
                    )

                response = offline.create(
                    model="gpt-4.1-mini",
                    input="Say hello",
                    temperature=0,
                    max_output_tokens=16,
                )

            self.assertEqual(response.output_text, "Hello from fake OpenAI.")

    def test_replay_rejects_params_mismatch_when_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_params_mismatch_fixture(Path(tempdir))
            offline = OfflineResponses()

            with self.assertRaisesRegex(OpenAIReplayDivergenceError, "params mismatch"):
                with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Say hello",
                        temperature=1,
                        max_output_tokens=16,
                    )

    def test_replay_rejects_extra_request_after_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _record_success_cassette(Path(tempdir))
            offline = OfflineResponses()

            with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                offline.create(
                    model="gpt-4.1-mini",
                    input="Say hello",
                    temperature=0,
                    max_output_tokens=16,
                )
                with self.assertRaisesRegex(OpenAIReplayDivergenceError, "replay exhausted"):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Say hello",
                        temperature=0,
                        max_output_tokens=16,
                    )

    def test_replay_restores_patch_after_divergence(self) -> None:
        original = OfflineResponses.create

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _record_success_cassette(Path(tempdir))
            offline = OfflineResponses()

            with self.assertRaises(OpenAIReplayDivergenceError):
                with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Changed prompt",
                        temperature=0,
                        max_output_tokens=16,
                    )

        self.assertIs(OfflineResponses.create, original)

    def test_replay_rejects_hash_only_response(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_hash_only_response_fixture(Path(tempdir))
            offline = OfflineResponses()

            with self.assertRaisesRegex(OpenAIReplayError, "missing replayable output"):
                with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                    offline.create(
                        model="gpt-4.1-mini",
                        input="Say hello",
                        temperature=0,
                        max_output_tokens=16,
                    )

    def test_replay_rejects_unconsumed_exchanges_on_clean_exit(self) -> None:
        original = OfflineResponses.create

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_reused_span_fixture(Path(tempdir))
            offline = OfflineResponses()

            with self.assertRaisesRegex(OpenAIReplayDivergenceError, "not consumed"):
                with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                    response = offline.create(
                        model="gpt-4.1-mini",
                        input="first",
                        temperature=0,
                        max_output_tokens=16,
                    )
                    self.assertEqual(response.output_text, "first response")

        self.assertIs(OfflineResponses.create, original)

    def test_replay_ties_reused_span_responses_to_call_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_reused_span_fixture(Path(tempdir))
            offline = OfflineResponses()

            with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                first = offline.create(
                    model="gpt-4.1-mini",
                    input="first",
                    temperature=0,
                    max_output_tokens=16,
                )
                second = offline.create(
                    model="gpt-4.1-mini",
                    input="second",
                    temperature=0,
                    max_output_tokens=16,
                )

            self.assertEqual(first.output_text, "first response")
            self.assertEqual(second.output_text, "second response")

    def test_replay_rejects_invalid_cassette_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "invalid-schema.replay.jsonl"
            events = [
                {
                    "schema_version": "0.2",
                    "event": "trace.start",
                    "trace_id": "tr_invalid",
                    "name": "invalid",
                },
                {
                    "schema_version": "0.2",
                    "event": "llm.call",
                    "trace_id": "tr_invalid",
                    "span_id": "sp_1",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "input_hash": _request_hash("Say hello"),
                    "params": {"temperature": 0, "max_output_tokens": 16},
                },
                {
                    "schema_version": "0.2",
                    "event": "llm.response",
                    "trace_id": "tr_invalid",
                    "span_id": "sp_1",
                    "output": {"text": "invalid"},
                },
                {
                    "schema_version": "0.2",
                    "event": "trace.end",
                    "trace_id": "tr_invalid",
                    "status": "success",
                },
            ]
            cassette.write_text(
                "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(OpenAIReplayError, "unsupported schema_version"):
                with replaying_openai(cassette, patch_target=(OfflineResponses, "create")):
                    pass

    def test_agent_and_tool_payloads_redact_secret_like_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "payload-redaction.replay.jsonl"

            with recording_openai(
                cassette,
                name="payload-redaction",
                patch_target=(FakeResponses, "create"),
            ):
                record_agent_step(
                    "prepare",
                    input={
                        "note": "Bearer secret-token.123",
                        "url": "postgres://user:pass@db/app",
                        "env": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
                        "AWS_SECRET_ACCESS_KEY": "drop-secret-key",
                        "apiKey": "drop-api-key",
                        "accessKey": "drop-access-key",
                        "privateKey": "drop-private-key",
                        "pwd": "drop-pwd",
                        "passwd": "drop-passwd",
                        "sessionToken": "drop-session-token",
                        "csrfToken": "drop-csrf-token",
                        "max_output_tokens": 16,
                        "session_cookie": "sid=abc123; HttpOnly",
                        "cookies": "sid=def456; Secure",
                        "set-cookie": "sid=ghi789; HttpOnly",
                        "body": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----",
                        "json_payload": '{"password":"hunter2","api_key":"plain-secret","safe":"ok"}',
                        "nested": ["prefix sk-agentsecret123456"],
                        "sk-agentkeysecret123456": "drop-key",
                        "safe": {
                            "Bearer nested-key-token.123": "drop-nested-key",
                            "ok": "keep",
                        },
                    },
                    output={"safe": "ok"},
                )
                with recording_tool(
                    "lookup",
                    input={"note": "Bearer tool-token.123"},
                    metadata={
                        "description": "uses sk-metasecret123456",
                        "sk-metakeysecret123456": "drop-key",
                    },
                ) as tool:
                    tool.set_output({
                        "msg": "sk-toolsecret123456",
                        "sk-outputkeysecret123456": "drop-key",
                    })

            events = _read_events(cassette)
            self.assertEqual(events[1]["input"]["note"], "[REDACTED]")
            self.assertEqual(events[1]["input"]["url"], "[REDACTED]")
            self.assertEqual(events[1]["input"]["env"], "[REDACTED]")
            self.assertNotIn("access_key_id", events[1]["input"])
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", events[1]["input"])
            self.assertNotIn("apiKey", events[1]["input"])
            self.assertNotIn("accessKey", events[1]["input"])
            self.assertNotIn("privateKey", events[1]["input"])
            self.assertNotIn("pwd", events[1]["input"])
            self.assertNotIn("passwd", events[1]["input"])
            self.assertNotIn("sessionToken", events[1]["input"])
            self.assertNotIn("csrfToken", events[1]["input"])
            self.assertEqual(events[1]["input"]["max_output_tokens"], 16)
            self.assertNotIn("session_cookie", events[1]["input"])
            self.assertNotIn("cookies", events[1]["input"])
            self.assertNotIn("set-cookie", events[1]["input"])
            self.assertEqual(events[1]["input"]["body"], "[REDACTED]")
            self.assertNotIn("hunter2", events[1]["input"]["json_payload"])
            self.assertNotIn("plain-secret", events[1]["input"]["json_payload"])
            self.assertIn('"safe":"ok"', events[1]["input"]["json_payload"])
            self.assertEqual(events[1]["input"]["nested"], ["prefix [REDACTED]"])
            self.assertNotIn("sk-agentkeysecret123456", events[1]["input"])
            self.assertNotIn("Bearer nested-key-token.123", events[1]["input"]["safe"])
            self.assertEqual(events[1]["input"]["safe"]["ok"], "keep")
            self.assertEqual(events[2]["input"]["note"], "[REDACTED]")
            self.assertEqual(events[2]["metadata"]["description"], "uses [REDACTED]")
            self.assertNotIn("sk-metakeysecret123456", events[2]["metadata"])
            self.assertEqual(events[3]["output"], {"msg": "[REDACTED]"})
            self.assertEqual(events[3]["output_hash"], hash_value({"msg": "[REDACTED]"}))

            raw = cassette.read_text(encoding="utf-8")
            self.assertNotIn("secret-token", raw)
            self.assertNotIn("postgres://", raw)
            self.assertNotIn("user:pass", raw)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", raw)
            self.assertNotIn("wJalrXUtn", raw)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", raw)
            self.assertNotIn("drop-api-key", raw)
            self.assertNotIn("drop-access-key", raw)
            self.assertNotIn("drop-private-key", raw)
            self.assertNotIn("drop-pwd", raw)
            self.assertNotIn("drop-passwd", raw)
            self.assertNotIn("drop-session-token", raw)
            self.assertNotIn("drop-csrf-token", raw)
            self.assertNotIn("sid=abc123", raw)
            self.assertNotIn("sid=def456", raw)
            self.assertNotIn("sid=ghi789", raw)
            self.assertNotIn("OPENSSH PRIVATE KEY", raw)
            self.assertNotIn("hunter2", raw)
            self.assertNotIn("plain-secret", raw)
            self.assertNotIn("sk-agentsecret", raw)
            self.assertNotIn("sk-agentkeysecret", raw)
            self.assertNotIn("nested-key-token", raw)
            self.assertNotIn("tool-token", raw)
            self.assertNotIn("sk-metasecret", raw)
            self.assertNotIn("sk-metakeysecret", raw)
            self.assertNotIn("sk-toolsecret", raw)
            self.assertNotIn("sk-outputkeysecret", raw)

            _validate_with_go(cassette)

    def test_tool_none_output_records_validator_compatible_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "none-tool-output.replay.jsonl"

            with recording_openai(
                cassette,
                name="none-tool-output",
                patch_target=(FakeResponses, "create"),
            ):
                with recording_tool("maybe_none", input={"id": "tool-1"}) as tool:
                    tool.set_output(None)

            events = _read_events(cassette)
            self.assertEqual(
                [event["event"] for event in events],
                ["trace.start", "tool.call", "tool.response", "trace.end"],
            )
            self.assertEqual(events[2]["output"], {"value": None})
            self.assertEqual(events[2]["output_hash"], hash_value({"value": None}))

            _validate_with_go(cassette)

    def test_tool_unsupported_outputs_record_safe_marker(self) -> None:
        marker = {
            "value_unavailable": True,
            "reason": "unsupported_type",
        }

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "unsupported-tool-output.replay.jsonl"

            with recording_openai(
                cassette,
                name="unsupported-tool-output",
                patch_target=(FakeResponses, "create"),
            ):
                with recording_tool("path_output") as tool:
                    tool.set_output(Path("/tmp/sk-pathsecret123456"))
                with recording_tool("decimal_output") as tool:
                    tool.set_output(Decimal("12.3"))
                with recording_tool("object_output") as tool:
                    tool.set_output(SecretRepr())

            events = _read_events(cassette)
            self.assertEqual(events[2]["output"], marker)
            self.assertEqual(events[2]["output_hash"], hash_value(marker))
            self.assertEqual(events[4]["output"], marker)
            self.assertEqual(events[6]["output"], marker)

            raw = cassette.read_text(encoding="utf-8")
            self.assertNotIn("sk-pathsecret", raw)
            self.assertNotIn("sk-reprsecret", raw)

            _validate_with_go(cassette)

    def test_failed_responses_create_records_error_and_restores_patch(self) -> None:
        original = FailingResponses.create

        with tempfile.TemporaryDirectory() as tempdir:
            cassette = Path(tempdir) / "error.replay.jsonl"
            fake = FailingResponses()

            with self.assertRaises(RuntimeError):
                with recording_openai(
                    cassette,
                    name="unit-error",
                    patch_target=(FailingResponses, "create"),
                ):
                    fake.create(model="gpt-4.1-mini", input="Say hello")

            self.assertIs(FailingResponses.create, original)
            events = _read_events(cassette)
            self.assertEqual(
                [event["event"] for event in events],
                ["trace.start", "llm.call", "llm.response", "trace.end"],
            )
            self.assertEqual(events[2]["error"], "RuntimeError: boom [REDACTED]")
            self.assertEqual(events[3]["status"], "error")
            self.assertNotIn("sk-testsecret", cassette.read_text(encoding="utf-8"))

            _validate_with_go(cassette)


def _read_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _record_success_cassette(directory: Path) -> Path:
    cassette = directory / "run.replay.jsonl"
    fake = FakeResponses()
    with recording_openai(cassette, name="unit", patch_target=(FakeResponses, "create")):
        fake.create(
            model="gpt-4.1-mini",
            input="Say hello",
            temperature=0,
            max_output_tokens=16,
        )
    return cassette


def _write_params_mismatch_fixture(directory: Path) -> Path:
    cassette = directory / "params-mismatch.replay.jsonl"
    events = [
        {
            "schema_version": "0.1",
            "event": "trace.start",
            "trace_id": "tr_params",
            "name": "params-mismatch",
        },
        {
            "schema_version": "0.1",
            "event": "llm.call",
            "trace_id": "tr_params",
            "span_id": "sp_1",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "input_hash": hash_value(
                {
                    "model": "gpt-4.1-mini",
                    "input": "Say hello",
                    "temperature": 1,
                    "max_output_tokens": 16,
                }
            ),
            "params": {"temperature": 0, "max_output_tokens": 16},
        },
        {
            "schema_version": "0.1",
            "event": "llm.response",
            "trace_id": "tr_params",
            "span_id": "sp_1",
            "output": {"text": "Hello from fixture."},
        },
        {
            "schema_version": "0.1",
            "event": "trace.end",
            "trace_id": "tr_params",
            "status": "success",
        },
    ]
    return _write_events(cassette, events)


def _write_hash_only_response_fixture(directory: Path) -> Path:
    cassette = directory / "hash-only.replay.jsonl"
    events = [
        {
            "schema_version": "0.1",
            "event": "trace.start",
            "trace_id": "tr_hash_only",
            "name": "hash-only",
        },
        {
            "schema_version": "0.1",
            "event": "llm.call",
            "trace_id": "tr_hash_only",
            "span_id": "sp_1",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "input_hash": _request_hash("Say hello"),
            "params": {"temperature": 0, "max_output_tokens": 16},
        },
        {
            "schema_version": "0.1",
            "event": "llm.response",
            "trace_id": "tr_hash_only",
            "span_id": "sp_1",
            "output_hash": hash_value({"text": "Hello from fixture."}),
        },
        {
            "schema_version": "0.1",
            "event": "trace.end",
            "trace_id": "tr_hash_only",
            "status": "success",
        },
    ]
    return _write_events(cassette, events)


def _write_reused_span_fixture(directory: Path) -> Path:
    cassette = directory / "reused-span.replay.jsonl"
    events = [
        {
            "schema_version": "0.1",
            "event": "trace.start",
            "trace_id": "tr_reused",
            "name": "reused-span",
        },
        {
            "schema_version": "0.1",
            "event": "llm.call",
            "trace_id": "tr_reused",
            "span_id": "sp_1",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "input_hash": _request_hash("first"),
            "params": {"temperature": 0, "max_output_tokens": 16},
        },
        {
            "schema_version": "0.1",
            "event": "llm.response",
            "trace_id": "tr_reused",
            "span_id": "sp_1",
            "output": {"text": "first response"},
        },
        {
            "schema_version": "0.1",
            "event": "llm.call",
            "trace_id": "tr_reused",
            "span_id": "sp_1",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "input_hash": _request_hash("second"),
            "params": {"temperature": 0, "max_output_tokens": 16},
        },
        {
            "schema_version": "0.1",
            "event": "llm.response",
            "trace_id": "tr_reused",
            "span_id": "sp_1",
            "output": {"text": "second response"},
        },
        {
            "schema_version": "0.1",
            "event": "trace.end",
            "trace_id": "tr_reused",
            "status": "success",
        },
    ]
    return _write_events(cassette, events)


def _request_hash(input_text: str) -> str:
    return hash_value(
        {
            "model": "gpt-4.1-mini",
            "input": input_text,
            "temperature": 0,
            "max_output_tokens": 16,
        }
    )


def _write_events(cassette: Path, events: list[dict]) -> Path:
    cassette.write_text(
        "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    _validate_with_go(cassette)
    return cassette


def _validate_with_go(path: Path) -> None:
    subprocess.run(
        ["go", "run", "./cmd/agentreplay", "validate", str(path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
