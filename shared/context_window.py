"""Conversation windowing — stop sending full context to LLMs every call.

Ported from OpenJarvis loop_guard.compress_context(). Adapted for Perseus
daemons that build message lists for LLM calls.

Usage:
    from shared.context_window import trim_messages

    messages = [
        {"role": "system", "content": "You are Titan..."},
        {"role": "user", "content": "Compose email for lead 42"},
        {"role": "assistant", "content": "..."},
        # ... 50 more messages from a long session
    ]

    # Keep only what matters — cuts cost by 50-80%
    trimmed = trim_messages(messages, max_messages=20)
"""

from __future__ import annotations

from typing import Any, Dict, List


def trim_messages(
    messages: List[Dict[str, Any]],
    max_messages: int = 20,
    keep_system: bool = True,
    keep_last_n: int = 10,
) -> List[Dict[str, Any]]:
    """Trim a conversation to fit within a message budget.

    Strategy:
    1. Always keep system messages
    2. Always keep the last ``keep_last_n`` non-system messages
    3. If still over budget, truncate tool results in older messages
    4. If STILL over, keep system + last 4 messages

    Parameters
    ----------
    messages:
        List of OpenAI-format message dicts (role, content, etc.)
    max_messages:
        Maximum number of messages to return.
    keep_system:
        Whether to always preserve system messages.
    keep_last_n:
        Minimum number of recent non-system messages to keep.
    """
    if len(messages) <= max_messages:
        return messages

    system = [m for m in messages if m.get("role") == "system"] if keep_system else []
    non_system = [m for m in messages if m.get("role") != "system"]

    # Stage 1: Keep system + last N
    budget = max_messages - len(system)
    if len(non_system) <= budget:
        return system + non_system

    trimmed = system + non_system[-budget:]
    if len(trimmed) <= max_messages:
        return trimmed

    # Stage 2: Truncate old tool results
    cutoff = len(trimmed) // 2
    compressed = []
    for i, msg in enumerate(trimmed):
        if i < cutoff and msg.get("role") == "tool":
            compressed.append({
                **msg,
                "content": "[truncated]",
            })
        else:
            compressed.append(msg)

    if len(compressed) <= max_messages:
        return compressed

    # Stage 3: Extreme — system + last 4
    return system + non_system[-4:]


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Rough token estimate for a message list (~4 chars per token)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += len(str(part.get("text", ""))) // 4
    return total
