from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root / "python"))

from agentreplay import record_agent_step, recording_openai, recording_tool, replaying_openai


DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_TOPIC = "agent replay regression testing"


def main() -> None:
    load_env_file(repo_root / ".env.local")

    mode = os.getenv("AGENTREPLAY_MODE", "record")
    cassette_path = Path(
        os.getenv(
            "AGENTREPLAY_CASSETTE",
            str(repo_root / "tmp" / "langgraph-demo.replay.jsonl"),
        )
    )
    topic = os.getenv("AGENTREPLAY_DEMO_TOPIC", DEFAULT_TOPIC)

    result = run_demo(
        mode=mode,
        cassette_path=cassette_path,
        client=openai_client(mode),
        topic=topic,
        model=os.getenv("AGENTREPLAY_OPENAI_MODEL", DEFAULT_MODEL),
    )

    print(f"{result['verb']} {cassette_path}")
    print(f"runtime: {result['runtime']}")
    print(f"answer: {result['answer']}")


def run_demo(
    *,
    mode: str,
    cassette_path: Path,
    client: Any,
    topic: str = DEFAULT_TOPIC,
    model: str = DEFAULT_MODEL,
    patch_target: tuple[type, str] | None = None,
) -> dict[str, Any]:
    if mode == "record":
        context = recording_openai(
            cassette_path,
            name="langgraph-demo",
            metadata={"framework": "langgraph", "example": "tool-llm"},
            patch_target=patch_target,
        )
        verb = "wrote"
    elif mode == "replay":
        context = replaying_openai(cassette_path, patch_target=patch_target)
        verb = "replayed"
    else:
        raise SystemExit(f"unsupported AGENTREPLAY_MODE {mode!r}")

    with context:
        result = run_agent(client, topic=topic, model=model)

    result["verb"] = verb
    return result


def run_agent(client: Any, *, topic: str, model: str) -> dict[str, Any]:
    initial_state = {
        "topic": topic,
        "model": model,
    }

    langgraph_app = _build_langgraph_app(client)
    if langgraph_app is not None:
        state = dict(initial_state)
        state["runtime"] = "langgraph"
        return dict(langgraph_app.invoke(state))

    state = dict(initial_state)
    state["runtime"] = "python-fallback"
    state = _lookup_node(state, client)
    state = _answer_node(state, client)
    return state


def _build_langgraph_app(client: Any) -> Any | None:
    try:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(dict)
        graph.add_node("lookup", lambda state: _lookup_node(dict(state), client))
        graph.add_node("answer", lambda state: _answer_node(dict(state), client))
        graph.add_edge(START, "lookup")
        graph.add_edge("lookup", "answer")
        graph.add_edge("answer", END)
        return graph.compile()
    except Exception:
        return None


def _lookup_node(state: dict[str, Any], client: Any) -> dict[str, Any]:
    del client

    topic = str(state.get("topic", DEFAULT_TOPIC))
    record_agent_step(
        "lookup_fact",
        metadata={"framework": "langgraph", "node": "lookup"},
        input={"topic": topic},
    )

    with recording_tool("demo_fact_lookup", input={"topic": topic}) as tool:
        fact = lookup_fact(topic)
        tool.set_output({"fact": fact})

    next_state = dict(state)
    next_state["fact"] = fact
    return next_state


def _answer_node(state: dict[str, Any], client: Any) -> dict[str, Any]:
    topic = str(state.get("topic", DEFAULT_TOPIC))
    fact = str(state.get("fact", lookup_fact(topic)))
    model = str(state.get("model", DEFAULT_MODEL))

    prompt = (
        "You are running the AgentReplay LangGraph demo.\n"
        f"Topic: {topic}\n"
        f"Tool fact: {fact}\n"
        "Return one concise sentence that uses the tool fact."
    )
    record_agent_step(
        "draft_answer",
        metadata={"framework": "langgraph", "node": "answer"},
        input={"topic": topic, "fact": fact},
    )
    response = client.responses.create(
        model=model,
        input=prompt,
        temperature=0,
        max_output_tokens=80,
    )

    answer = _response_text(response)
    record_agent_step(
        "final_answer",
        metadata={"framework": "langgraph", "node": "answer"},
        output={"answer": answer},
    )

    next_state = dict(state)
    next_state["answer"] = answer
    return next_state


def lookup_fact(topic: str) -> str:
    facts = {
        DEFAULT_TOPIC: "Replay cassettes turn an agent run into a deterministic regression fixture.",
        "refund policy": "Refund policy answers should mention the original order and support window.",
    }
    return facts.get(
        topic.lower(),
        f"{topic} should be grounded by a recorded tool lookup before the LLM answers.",
    )


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    if isinstance(response, dict):
        value = response.get("output_text")
        if isinstance(value, str):
            return value
    return str(response)


def openai_client(mode: str) -> Any:
    from openai import OpenAI

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
