from __future__ import annotations

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root / "python"))

from openai import OpenAI

from agentreplay.openai_hook import recording_openai, replaying_openai


def main() -> None:
    load_env_file(repo_root / ".env.local")

    mode = os.getenv("AGENTREPLAY_MODE", "record")
    cassette_path = Path(os.getenv("AGENTREPLAY_CASSETTE", str(repo_root / "tmp" / "openai-smoke.replay.jsonl")))
    client = openai_client(mode)

    if mode == "record":
        context = recording_openai(cassette_path, name="openai-smoke")
        verb = "wrote"
    elif mode == "replay":
        context = replaying_openai(cassette_path)
        verb = "replayed"
    else:
        raise SystemExit(f"unsupported AGENTREPLAY_MODE {mode!r}")

    with context:
        response = client.responses.create(
            model=os.getenv("AGENTREPLAY_OPENAI_MODEL", "gpt-4.1-mini"),
            input="Reply with exactly: agentreplay-ok",
            temperature=0,
            max_output_tokens=16,
        )

    print(f"{verb} {cassette_path}")
    print(f"response: {response.output_text}")


def openai_client(mode: str) -> OpenAI:
    if mode == "replay":
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY", "agentreplay-offline"))
    return OpenAI()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()
