from __future__ import annotations

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root / "python"))

from openai import OpenAI

from agentreplay.openai_hook import replaying_openai
from openai_record_smoke import load_env_file


def main() -> None:
    load_env_file(repo_root / ".env.local")

    cassette_path = repo_root / "tmp" / "openai-smoke.replay.jsonl"
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "agentreplay-offline"))

    with replaying_openai(cassette_path):
        response = client.responses.create(
            model=os.getenv("AGENTREPLAY_OPENAI_MODEL", "gpt-4.1-mini"),
            input="Reply with exactly: agentreplay-ok",
            temperature=0,
            max_output_tokens=16,
        )

    print(f"replayed {cassette_path}")
    print(f"response: {response.output_text}")


if __name__ == "__main__":
    main()
