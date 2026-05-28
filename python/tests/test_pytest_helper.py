from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentreplay.hashing import hash_value
from agentreplay.openai_hook import OpenAIReplayError
from agentreplay.pytest import replay_case


class PytestHelperTests(unittest.TestCase):
    def test_replay_case_accepts_replayable_cassette(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_events(
                Path(tempdir) / "run.replay.jsonl",
                [
                    {
                        "schema_version": "0.1",
                        "event": "trace.start",
                        "trace_id": "tr_pytest",
                        "name": "pytest-helper",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.call",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "input_hash": "sha256:input",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.response",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "output": {"text": "ok"},
                    },
                    {
                        "schema_version": "0.1",
                        "event": "trace.end",
                        "trace_id": "tr_pytest",
                        "status": "success",
                    },
                ],
            )

            result = replay_case(cassette)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.divergence_count, 0)
        self.assertEqual(result.llm_exchange_count, 1)

    def test_replay_case_rejects_hash_only_response(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_events(
                Path(tempdir) / "hash-only.replay.jsonl",
                [
                    {
                        "schema_version": "0.1",
                        "event": "trace.start",
                        "trace_id": "tr_pytest",
                        "name": "pytest-helper",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.call",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "input_hash": "sha256:input",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.response",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "output_hash": hash_value({"text": "ok"}),
                    },
                    {
                        "schema_version": "0.1",
                        "event": "trace.end",
                        "trace_id": "tr_pytest",
                        "status": "success",
                    },
                ],
            )

            with self.assertRaisesRegex(OpenAIReplayError, "missing replayable output"):
                replay_case(cassette)

    def test_replay_case_rejects_unreplayable_responses(self) -> None:
        cases = [
            (
                "error-response.replay.jsonl",
                {"error": "RuntimeError: boom"},
                "contains error",
            ),
            (
                "null-output.replay.jsonl",
                {"output": None},
                "null output",
            ),
        ]

        for filename, response_fields, error_pattern in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tempdir:
                response_event = {
                    "schema_version": "0.1",
                    "event": "llm.response",
                    "trace_id": "tr_pytest",
                    "span_id": "sp_1",
                }
                response_event.update(response_fields)
                cassette = _write_events(
                    Path(tempdir) / filename,
                    [
                        {
                            "schema_version": "0.1",
                            "event": "trace.start",
                            "trace_id": "tr_pytest",
                            "name": "pytest-helper",
                        },
                        {
                            "schema_version": "0.1",
                            "event": "llm.call",
                            "trace_id": "tr_pytest",
                            "span_id": "sp_1",
                            "provider": "openai",
                            "model": "gpt-4.1-mini",
                            "input_hash": "sha256:input",
                        },
                        response_event,
                        {
                            "schema_version": "0.1",
                            "event": "trace.end",
                            "trace_id": "tr_pytest",
                            "status": "success",
                        },
                    ],
                )

                with self.assertRaisesRegex(OpenAIReplayError, error_pattern):
                    replay_case(cassette)

    def test_replay_case_rejects_malformed_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_events(
                Path(tempdir) / "malformed-call.replay.jsonl",
                [
                    {
                        "schema_version": "0.1",
                        "event": "trace.start",
                        "trace_id": "tr_pytest",
                        "name": "pytest-helper",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.call",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "provider": "openai",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.response",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "output": {"text": "ok"},
                    },
                    {
                        "schema_version": "0.1",
                        "event": "trace.end",
                        "trace_id": "tr_pytest",
                        "status": "success",
                    },
                ],
            )

            with self.assertRaisesRegex(OpenAIReplayError, "missing replayable model"):
                replay_case(cassette)

    def test_replay_case_rejects_non_object_llm_params(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            cassette = _write_events(
                Path(tempdir) / "malformed-params.replay.jsonl",
                [
                    {
                        "schema_version": "0.1",
                        "event": "trace.start",
                        "trace_id": "tr_pytest",
                        "name": "pytest-helper",
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.call",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "input_hash": "sha256:input",
                        "params": ["temperature", 0],
                    },
                    {
                        "schema_version": "0.1",
                        "event": "llm.response",
                        "trace_id": "tr_pytest",
                        "span_id": "sp_1",
                        "output": {"text": "ok"},
                    },
                    {
                        "schema_version": "0.1",
                        "event": "trace.end",
                        "trace_id": "tr_pytest",
                        "status": "success",
                    },
                ],
            )

            with self.assertRaisesRegex(OpenAIReplayError, "non-object params"):
                replay_case(cassette)


def _write_events(path: Path, events: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
