from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import replace

from openjarvis.core.registry import CompressionRegistry
from openjarvis.core.types import Message, Role

logger = logging.getLogger(__name__)


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
        except (OSError, RuntimeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
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


# ---------------------------------------------------------------------------
# Phase 31: BACM-lite — Hierarchical Importance Compression
# ---------------------------------------------------------------------------

def _bacm_enabled() -> bool:
    return os.environ.get("BACM_COMPRESSION", "true").lower() in ("true", "1", "yes")


def score_segment_importance(
    segment: list[dict],
    segment_index: int,
    total_segments: int,
    current_task: str | None,
) -> float:
    """Score 0.0 (expendable) to 1.0 (critical). BACM-lite heuristic.

    Scoring dimensions:
    - Recency (35%): newer segments are more important
    - Tool results (25%): segments with tool outputs are more important
    - Task relevance (25%): segments mentioning current task keywords
    - System/user instructions (15%): always high importance
    """
    score = 0.0

    # Recency: newer segments are more important
    recency = segment_index / max(total_segments - 1, 1)
    score += recency * 0.35

    # Tool results: segments with tool outputs are more important
    has_tool_result = any(m.get("role") == "tool" for m in segment)
    if has_tool_result:
        score += 0.25

    # Task relevance: segments mentioning current task keywords
    if current_task:
        task_words = set(current_task.lower().split())
        segment_text = " ".join(str(m.get("content", "")) for m in segment).lower()
        segment_words = set(segment_text.split())
        overlap = len(task_words & segment_words) / max(len(task_words), 1)
        score += overlap * 0.25

    # System/user instructions: always high importance
    has_system = any(m.get("role") == "system" for m in segment)
    if has_system:
        score += 0.15

    return min(score, 1.0)


class BACMCompressor:
    """Budget-Aware Context Management (lightweight heuristic variant).

    Compression intensity scales with budget_ratio:
    - >0.5: no compression (NULL action)
    - 0.25-0.5: compress bottom 40% by importance (PARTIAL)
    - 0.10-0.25: compress bottom 70% by importance (PARTIAL aggressive)
    - <0.10: compress everything except current segment (FULL)

    Feature flag: BACM_COMPRESSION (default true)
    """

    def compress(
        self,
        messages: list[dict],
        budget_ratio: float,
        current_task: str | None = None,
    ) -> list[dict]:
        """Compress messages based on budget ratio and segment importance.

        Args:
            messages: list of message dicts with 'role' and 'content' keys
            budget_ratio: remaining_tokens / context_window (0.0-1.0)
            current_task: optional task description for relevance scoring

        Returns:
            Compressed list of message dicts
        """
        if not _bacm_enabled():
            return messages

        if not messages or budget_ratio > 0.5:
            return messages  # NULL action: no compression needed

        segments = self._segment_messages(messages)
        if len(segments) <= 1:
            return messages

        scores = [
            score_segment_importance(seg, i, len(segments), current_task)
            for i, seg in enumerate(segments)
        ]

        if budget_ratio > 0.25:
            # PARTIAL: compress bottom 40% by importance
            threshold_idx = int(len(scores) * 0.4)
            threshold = sorted(scores)[min(threshold_idx, len(scores) - 1)]
            return self._compress_below(segments, scores, threshold)

        if budget_ratio > 0.10:
            # PARTIAL aggressive: compress bottom 70%
            threshold_idx = int(len(scores) * 0.7)
            threshold = sorted(scores)[min(threshold_idx, len(scores) - 1)]
            return self._compress_below(segments, scores, threshold)

        # FULL: compress everything except current (last) segment
        return self._compress_all_except_current(segments, scores)

    def compress_messages(
        self,
        messages: list,
        budget_ratio: float,
        current_task: str | None = None,
    ) -> list:
        """Compress Message objects (not dicts) — converts internally.

        Convenience wrapper for use with LoopGuard which passes Message objects.
        Returns Message objects.
        """
        if not _bacm_enabled() or not messages or budget_ratio > 0.5:
            return messages

        # Convert Message objects to dicts
        msg_dicts = []
        for m in messages:
            d = {
                "role": getattr(m, "role", "unknown"),
                "content": getattr(m, "content", ""),
            }
            if hasattr(m, "tool_call_id") and m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if hasattr(m, "name") and m.name:
                d["name"] = m.name
            msg_dicts.append(d)

        compressed_dicts = self.compress(msg_dicts, budget_ratio, current_task)

        # If no compression happened, return original
        if len(compressed_dicts) == len(msg_dicts):
            return messages

        # Convert back to Message objects
        result = []
        for d in compressed_dicts:
            role_val = d.get("role", "user")
            # Handle both string and enum role values
            role = role_val if isinstance(role_val, Role) else Role(str(role_val))
            kwargs = {"role": role, "content": d.get("content", "")}
            if d.get("tool_call_id"):
                kwargs["tool_call_id"] = d["tool_call_id"]
            if d.get("name"):
                kwargs["name"] = d["name"]
            result.append(Message(**kwargs))
        return result

    @staticmethod
    def _segment_messages(messages: list[dict]) -> list[list[dict]]:
        """Segment messages by tool-call/response pairs.

        Each tool interaction (assistant with tool_calls + tool response) forms
        one segment. Non-tool messages form segments of 10 messages each
        (fallback for non-tool conversations).
        """
        segments: list[list[dict]] = []
        current_segment: list[dict] = []

        for msg in messages:
            current_segment.append(msg)
            role = msg.get("role", "")

            # Segment boundary: after tool response
            if role == "tool":
                segments.append(current_segment)
                current_segment = []
            # Fallback: chunk every 10 messages for non-tool conversations
            elif len(current_segment) >= 10 and role != "system":
                segments.append(current_segment)
                current_segment = []

        if current_segment:
            segments.append(current_segment)

        return segments

    @staticmethod
    def _compress_below(
        segments: list[list[dict]],
        scores: list[float],
        threshold: float,
    ) -> list[dict]:
        """Compress segments scoring below threshold into summaries."""
        result: list[dict] = []
        for seg, score in zip(segments, scores):  # noqa: B905 — Python 3.9 compat
            if score < threshold:
                # Summarize: keep first message's role + truncated content
                summary_parts = []
                for m in seg:
                    content = str(m.get("content", ""))
                    summary_parts.append(f"[{m.get('role', '?')}]: {content[:80]}")
                result.append({
                    "role": "system",
                    "content": f"[Compressed segment, importance={score:.2f}] "
                    + "; ".join(summary_parts),
                })
            else:
                result.extend(seg)
        return result

    @staticmethod
    def _compress_all_except_current(
        segments: list[list[dict]],
        scores: list[float],
    ) -> list[dict]:
        """Compress all segments except the most recent one."""
        if not segments:
            return []

        result: list[dict] = []

        # Summarize all but last segment
        for seg, score in zip(segments[:-1], scores[:-1]):  # noqa: B905 — Python 3.9 compat
            summary_parts = []
            for m in seg:
                content = str(m.get("content", ""))
                summary_parts.append(f"[{m.get('role', '?')}]: {content[:50]}")
            result.append({
                "role": "system",
                "content": f"[Compressed, importance={score:.2f}] "
                + "; ".join(summary_parts),
            })

        # Keep last segment intact
        result.extend(segments[-1])
        return result
