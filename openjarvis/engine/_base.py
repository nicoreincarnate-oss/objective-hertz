"""Shared engine utilities and re-exports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Dict, List

from openjarvis.core.types import Message
from openjarvis.engine._stubs import InferenceEngine


class EngineConnectionError(Exception):
    """Raised when an engine is unreachable."""


def messages_to_dicts(messages: Sequence[Message]) -> List[Dict[str, Any]]:
    """Convert ``Message`` objects to OpenAI-format dicts."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        d: Dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.name:
            d["name"] = m.name
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        out.append(d)
    return out


__all__ = ["EngineConnectionError", "InferenceEngine", "messages_to_dicts"]
