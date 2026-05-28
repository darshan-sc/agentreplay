"""Pytest-facing helpers for generated AgentReplay regression tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .openai_hook import ReplayIndex


@dataclass(frozen=True)
class ReplayCaseResult:
    cassette: str
    status: str
    divergence_count: int
    llm_exchange_count: int


def replay_case(path: str | os.PathLike[str]) -> ReplayCaseResult:
    """Validate that a cassette can be loaded for offline replay."""

    cassette = Path(path)
    index = ReplayIndex.from_path(cassette)
    index.assert_replayable()
    return ReplayCaseResult(
        cassette=str(cassette),
        status="passed",
        divergence_count=0,
        llm_exchange_count=index.llm_exchange_count,
    )


__all__ = ["ReplayCaseResult", "replay_case"]
