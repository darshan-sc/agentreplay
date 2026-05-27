from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentreplay.hashing import hash_json, hash_value


class HashingTests(unittest.TestCase):
    def test_hash_value_matches_go_canonical_fixture(self) -> None:
        self.assertEqual(
            hash_value({"a": 1, "b": 2}),
            "sha256:43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
        )

    def test_hash_json_canonicalizes_key_order_and_whitespace(self) -> None:
        first = hash_json('{"a":1,"b":2}')
        second = hash_json('{\n  "b": 2,\n  "a": 1\n}')

        self.assertEqual(first, second)

    def test_nested_fixture_matches_go_canonical_fixture(self) -> None:
        value = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0},
        }

        self.assertEqual(
            hash_value(value),
            "sha256:6ff041b4ca8d25b6faa00b8232ae5fe99ca780996c8c99e22e8ec46084a7a030",
        )

    def test_hash_rejects_non_json_values(self) -> None:
        with self.assertRaises(ValueError):
            hash_value({"bad": object()})


if __name__ == "__main__":
    unittest.main()
