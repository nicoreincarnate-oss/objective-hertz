from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import replace

from openjarvis.core.registry import CompressionRegistry
from openjarvis.core.types import Message, Role


class BaseCompressor(ABC):
    """Abstract base for context compression strategies."""

    @abstractmethod
    def compress(self, messages: list[Message], threshold: float) -> list[Message]:
        ...


@CompressionRegistry.register("session_consolidation")
class SessionConsolidation(BaseCompressor):
    """Summarize oldest N% of turns, keep recent (100-N)%."""

    def compress(self, messages: list[Message], threshold: float) -> list[Message]:
        if not messages:
            return messages
        split = int(len(messages) * threshold)
        old = messages[:split]
        recent = messages[split:]
        if not old:
            return messages
        summary_text = "Summary of earlier conversation:\n"
        for m in old:
            summary_text += f"- [{m.role}]: {m.content[:100]}...\n"
        summary = Message(role=Role.SYSTEM, content=summary_text)
        return [summary] + recent


@CompressionRegistry.register("rule_based_precompression")
class RuleBasedPrecompression(BaseCompressor):
    """No LLM call. Strip boilerplate, truncate long outputs, collapse dupes."""

    TOOL_OUTPUT_MAX = 2000

    def compress(self, messages: list[Message], threshold: float) -> list[Message]:
        result: list[Message] = []
        for msg in messages:
            if msg.role == Role.TOOL and len(msg.content) > self.TOOL_OUTPUT_MAX:
                suffix = "\n[...truncated]"
                try:
                    parsed = json.loads(msg.content)
                    truncated = (
                        json.dumps(parsed, indent=None)[
                            : self.TOOL_OUTPUT_MAX
                        ]
                        + suffix
                    )
                except (json.JSONDecodeError, TypeError):
                    truncated = (
                        msg.content[: self.TOOL_OUTPUT_MAX] + suffix
                    )
                result.append(replace(msg, content=truncated))
            else:
                result.append(msg)
        return result


@CompressionRegistry.register("model_summarization")
class ModelSummarization(BaseCompressor):
    """LLM-based summarization using configured engine/model.

    Falls back to SessionConsolidation if no engine is available.
    """

    def __init__(self, engine: object | None = None, model: str = "") -> None:
        self._engine = engine
        self._model = model

    def compress(self, messages: list[Message], threshold: float) -> list[Message]:
        if not messages:
            return messages

        split = int(len(messages) * threshold)
        old = messages[:split]
        recent = messages[split:]
        if not old:
            return messages

        # If no engine, fall back to rule-based summarization
        if self._engine is None or not hasattr(self._engine, "generate"):
            fallback = SessionConsolidation()
            return fallback.compress(messages, threshold)

        # Build a summarization prompt from old messages
        conversation = "\n".join(
            f"[{m.role}]: {m.content[:300]}" for m in old
        )
        prompt = (
            "Summarize the following conversation history in 2-3 sentences, "
            "capturing the key topics, decisions, and any pending questions:\n\n"
            f"{conversation}"
        )

        try:
            response = self._engine.generate(
                prompt=prompt,
                model=self._model,
                max_tokens=300,
                temperature=0.0,
            )
            summary_text = (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
        except Exception:
            # If model call fails, fall back
            fallback = SessionConsolidation()
            return fallback.compress(messages, threshold)

        summary = Message(role=Role.SYSTEM, content=f"[Session summary] {summary_text}")
        return [summary] + recent


@CompressionRegistry.register("tiered_summaries")
class TieredSummaries(BaseCompressor):
    """Progressive compression: L0 (full) -> L1 (paragraph) -> L2 (one-line)."""

    def compress(self, messages: list[Message], threshold: float) -> list[Message]:
        if not messages:
            return messages
        n = len(messages)
        l2_end = int(n * threshold * 0.5)
        l1_end = int(n * threshold)
        l2_msgs = messages[:l2_end]
        l1_msgs = messages[l2_end:l1_end]
        l0_msgs = messages[l1_end:]
        result: list[Message] = []
        if l2_msgs:
            one_liners = "; ".join(
                f"{m.role}: {m.content[:50]}" for m in l2_msgs
            )
            result.append(Message(
                role=Role.SYSTEM,
                content=f"[Oldest context] {one_liners}",
            ))
        if l1_msgs:
            paragraphs = "\n".join(
                f"- {m.role}: {m.content[:200]}" for m in l1_msgs
            )
            result.append(Message(
                role=Role.SYSTEM,
                content=f"[Earlier context]\n{paragraphs}",
            ))
        result.extend(l0_msgs)
        return result
