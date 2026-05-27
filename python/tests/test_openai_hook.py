from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentreplay.hashing import hash_value
from agentreplay.openai_hook import recording_openai

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
