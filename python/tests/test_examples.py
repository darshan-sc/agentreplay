from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python" / "examples"))

import langgraph_demo


class ExampleScriptTests(unittest.TestCase):
    def test_record_smoke_imports_local_package_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fake_openai = Path(tempdir) / "openai.py"
            fake_openai.write_text("class OpenAI:\n    pass\n", encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = tempdir

            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import runpy; runpy.run_path('python/examples/openai_record_smoke.py')",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_langgraph_demo_records_tool_steps_and_replays_offline(self) -> None:
        original_builder = langgraph_demo._build_langgraph_app
        langgraph_demo._build_langgraph_app = lambda client: None
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                cassette = Path(tempdir) / "langgraph-demo.replay.jsonl"

                record_result = langgraph_demo.run_demo(
                    mode="record",
                    cassette_path=cassette,
                    client=DemoClient(DemoResponses()),
                    topic="refund policy",
                    model="gpt-4.1-mini",
                    patch_target=(DemoResponses, "create"),
                )

                _validate_with_go(cassette)
                events = _read_events(cassette)
                self.assertEqual(
                    [event["event"] for event in events],
                    [
                        "trace.start",
                        "agent.step",
                        "tool.call",
                        "tool.response",
                        "agent.step",
                        "llm.call",
                        "llm.response",
                        "agent.step",
                        "trace.end",
                    ],
                )
                self.assertEqual(events[0]["metadata"]["framework"], "langgraph")
                self.assertEqual(events[1]["name"], "lookup_fact")
                self.assertEqual(events[2]["name"], "demo_fact_lookup")
                self.assertEqual(events[2]["input"], {"topic": "refund policy"})
                self.assertEqual(
                    events[3]["output"],
                    {
                        "fact": "Refund policy answers should mention the original order and support window.",
                    },
                )
                self.assertEqual(events[5]["provider"], "openai")
                self.assertEqual(events[5]["model"], "gpt-4.1-mini")
                self.assertEqual(events[6]["output"], {"text": DemoResponse.output_text})
                self.assertEqual(record_result["answer"], DemoResponse.output_text)

                replay_result = langgraph_demo.run_demo(
                    mode="replay",
                    cassette_path=cassette,
                    client=DemoClient(OfflineDemoResponses()),
                    topic="refund policy",
                    model="gpt-4.1-mini",
                    patch_target=(OfflineDemoResponses, "create"),
                )

            self.assertEqual(replay_result["answer"], record_result["answer"])
        finally:
            langgraph_demo._build_langgraph_app = original_builder


class DemoResponse:
    output_text = "Refund answers should cite the order and support window."

    class usage:
        input_tokens = 11
        output_tokens = 9
        total_tokens = 20


class DemoResponses:
    def create(self, **kwargs):
        if "Tool fact:" not in kwargs.get("input", ""):
            raise AssertionError("demo prompt should include the tool fact")
        return DemoResponse()


class OfflineDemoResponses:
    def create(self, **kwargs):
        raise AssertionError("live OpenAI method should not be called during replay")


class DemoClient:
    def __init__(self, responses) -> None:
        self.responses = responses


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
