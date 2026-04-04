"""Run the upstream ByteDance DeerFlow embedded client for Perseus research."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_environment(root: Path) -> None:
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "tools" / "deer-flow" / ".env", override=False)


def _prepare_deerflow(root: Path) -> None:
    harness_path = root / "tools" / "deer-flow" / "backend" / "packages" / "harness"
    if str(harness_path) not in sys.path:
        sys.path.insert(0, str(harness_path))
    os.environ.setdefault("DEER_FLOW_CONFIG_PATH", str(root / "tools" / "deer-flow" / "config.yaml"))
    os.environ.setdefault("DEER_FLOW_HOME", str(root / "data" / "deerflow_upstream"))


def _build_analysis_prompt(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "continuous")
    topic = str(payload.get("topic") or "Perseus evolution research")
    sources = ", ".join(payload.get("sources") or [])
    objectives = payload.get("objectives") or []
    items = payload.get("items") or []
    lines = [
        "You are ByteDance DeerFlow acting as the continuous R&D engine for Perseus.",
        "Analyze the latest research intake and decide how the system should evolve.",
        "",
        f"Mode: {mode}",
        f"Topic: {topic}",
        f"Sources: {sources or 'none provided'}",
        "",
        "Objectives:",
    ]
    for objective in objectives:
        lines.append(f"- {objective}")
    if not objectives:
        lines.append("- Produce the highest-leverage recommendations you can.")
    lines.extend(
        [
            "",
            "Fresh material:",
        ]
    )
    if items:
        for index, item in enumerate(items[:20], start=1):
            lines.extend(
                [
                    f"{index}. {item.get('title', 'Untitled')}",
                    f"   Source: {item.get('source', 'unknown')}",
                    f"   Published: {item.get('published_at', 'unknown')}",
                    f"   URL: {item.get('url', '')}",
                    f"   Tags: {', '.join(item.get('tags', []))}",
                    f"   Summary: {item.get('summary', '')}",
                ]
            )
    else:
        lines.append("- No fresh external items were captured. Research the backlog and unresolved questions instead.")
    lines.extend(
        [
            "",
            "Return Markdown with these sections only:",
            "## Situation",
            "## Highest-Leverage Signals",
            "## Ranked Adoption Candidates",
            "## Suggested Experiments In The Next 24 Hours",
            "## Open Questions To Keep Researching",
            "",
            "Be concrete. Name the subsystem each recommendation belongs to: Hermes, Titan, ClawdBot, OpenJarvis, Kirito, memory, or local inference.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    root = _repo_root()
    _load_environment(root)
    _prepare_deerflow(root)

    from deerflow.client import DeerFlowClient

    payload = json.loads(sys.stdin.read() or "{}")
    action = str(payload.get("action") or "health")
    client = DeerFlowClient(
        config_path=os.environ["DEER_FLOW_CONFIG_PATH"],
        thinking_enabled=True,
        subagent_enabled=False,
    )

    if action == "health":
        models = client.list_models()
        print(
            json.dumps(
                {
                    "ok": True,
                    "engine": "deerflow_client",
                    "metadata": {
                        "config_path": os.environ["DEER_FLOW_CONFIG_PATH"],
                        "deer_flow_home": os.environ["DEER_FLOW_HOME"],
                        "models": models.get("models", []),
                    },
                }
            )
        )
        return 0

    prompt = _build_analysis_prompt(payload)
    thread_id = str(payload.get("thread_id") or f"perseus-deerflow-{uuid.uuid4().hex[:10]}")
    response = client.chat(prompt, thread_id=thread_id)
    print(
        json.dumps(
            {
                "ok": True,
                "engine": "deerflow_client",
                "analysis_markdown": response,
                "metadata": {
                    "thread_id": thread_id,
                    "config_path": os.environ["DEER_FLOW_CONFIG_PATH"],
                    "mode": payload.get("mode"),
                    "topic": payload.get("topic"),
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
