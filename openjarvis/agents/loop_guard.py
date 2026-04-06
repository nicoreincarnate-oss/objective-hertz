"""Agent loop guard — detect and prevent degenerate tool-calling loops."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import deque
from dataclasses import dataclass

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model context-window sizes (tokens).  Leave 20 % headroom by default.
# ---------------------------------------------------------------------------
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "haiku": 200_000,
    "sonnet": 200_000,
    "opus": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000

# Thresholds expressed as fractions of the model context window.
_COMPRESS_THRESHOLD = 0.80   # 80 % → trigger graduated compression
_TRUNCATE_THRESHOLD = 0.95   # 95 % → hard truncation


def _context_window_for_model(model: str) -> int:
    """Return the context-window size for *model* (best-effort match)."""
    model_lower = model.lower()
    for key, size in MODEL_CONTEXT_WINDOWS.items():
        if key in model_lower:
            return size
    return _DEFAULT_CONTEXT_WINDOW


def _is_token_context_enabled() -> bool:
    """Check whether Phase-21 token-aware context management is active."""
    return os.environ.get("ANATOMY_AGENT_PERMISSIONS", "").lower() in ("true", "1")


def _estimate_tokens(messages: list) -> int:
    """Estimate total token count for a message list.

    Uses actual token counts stored in ``message.metadata["token_count"]``
    when available (populated from API ``usage`` data).  Falls back to a
    rough ``len(content) / 4`` heuristic otherwise.
    """
    total = 0
    for msg in messages:
        meta = getattr(msg, "metadata", None) or {}
        count = meta.get("token_count")
        if count is not None:
            total += int(count)
        else:
            content = getattr(msg, "content", "") or ""
            total += max(len(content) // 4, 1)
    return total


@dataclass
class LoopGuardConfig:
    """Configuration for the loop guard."""
    enabled: bool = True
    max_identical_calls: int = 3       # SHA-256 of (tool_name, arguments)
    ping_pong_window: int = 6          # detect A-B-A-B cycling
    poll_tool_budget: int = 5          # max calls to same polling tool
    max_context_messages: int = 100    # context overflow threshold (legacy)
    warn_before_block: bool = True     # warn on first cycle, block on second
    # Token-aware overrides (Phase 21).  When the feature flag is enabled
    # these take precedence over the message-count threshold.
    model: str = ""                    # model name for context-window lookup
    token_compress_ratio: float = _COMPRESS_THRESHOLD
    token_truncate_ratio: float = _TRUNCATE_THRESHOLD
    # Phase 31: current task description for BACM relevance scoring
    current_task: str | None = None


@dataclass
class LoopVerdict:
    """Result of a loop guard check."""
    blocked: bool = False
    reason: str = ""
    warned: bool = False


class LoopGuard:
    """Detect and prevent degenerate agent loops.

    Features:
    1. Hash tracking: SHA-256 of (tool_name, args) blocks after max_identical_calls
    2. Ping-pong detection: Sliding window detects A-B-A-B or A-B-C-A-B-C patterns
    3. Poll-tool awareness: Tools with spec.metadata["polling"] = True
       get relaxed budget
    4. Context overflow recovery: 4-stage compression of message history
       (token-aware when ANATOMY_AGENT_PERMISSIONS is enabled)
    """

    def __init__(self, config: LoopGuardConfig, *, bus: EventBus | None = None):
        self._config = config
        self._bus = bus
        # Track call hashes and their counts
        self._call_counts: dict[str, int] = {}
        # Track tool name sequence for pattern detection
        self._tool_sequence: deque[str] = deque(maxlen=config.ping_pong_window * 2)
        # Track per-tool call counts (for polling budget)
        self._per_tool_counts: dict[str, int] = {}
        # Track cycle keys that have already been warned (for warn-before-block)
        self._warned_cycles: set[str] = set()

        # Token-aware context management (Phase 21)
        self._context_window = _context_window_for_model(config.model)
        self._compress_limit = int(self._context_window * config.token_compress_ratio)
        self._truncate_limit = int(self._context_window * config.token_truncate_ratio)

        # Phase 31: BACM compressor for budget-aware context compression
        self._bacm: object | None = None
        self._budget: object | None = None  # set via set_budget()

        from openjarvis._rust_bridge import get_rust_module
        _rust = get_rust_module()
        if _rust is None:
            self._rust_impl = None
        else:
            try:
                self._rust_impl = _rust.LoopGuard(
                    max_identical=config.max_identical_calls,
                    max_ping_pong=(
                        config.ping_pong_window // 2
                        if config.ping_pong_window > 1
                        else 2
                    ),
                    poll_budget=config.poll_tool_budget,
                )
            except (ImportError, OSError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                self._rust_impl = None

    # ------------------------------------------------------------------
    # Public API — call checks
    # ------------------------------------------------------------------

    def check_call(self, tool_name: str, arguments: str) -> LoopVerdict:
        """Check whether a tool call should proceed or be blocked."""
        if self._rust_impl is not None:
            rust_result = self._rust_impl.check(tool_name, arguments)
            # Support both raw Rust return (str | None) and LoopVerdict
            if isinstance(rust_result, LoopVerdict):
                verdict = rust_result
            elif rust_result is not None:
                self._emit_triggered("rust_guard", tool_name)
                verdict = LoopVerdict(blocked=True, reason=rust_result)
            else:
                verdict = LoopVerdict()
        else:
            verdict = self._python_check(tool_name, arguments)

        # Wrap with warn-before-block logic
        if verdict.blocked and self._config.warn_before_block:
            cycle_key = verdict.reason
            if cycle_key not in self._warned_cycles:
                self._warned_cycles.add(cycle_key)
                return LoopVerdict(blocked=False, warned=True, reason=verdict.reason)
        return verdict

    def _python_check(self, tool_name: str, arguments: str) -> LoopVerdict:
        """Pure-Python fallback when Rust backend is not available."""
        # 1. Hash tracking — identical calls
        # Mirror the Rust backend behavior: block after max_identical_calls.
        call_hash = hashlib.sha256(
            f"{tool_name}:{arguments}".encode()
        ).hexdigest()[:16]
        self._call_counts[call_hash] = self._call_counts.get(call_hash, 0) + 1
        if self._call_counts[call_hash] > self._config.max_identical_calls:
            self._emit_triggered("identical_call", tool_name)
            return LoopVerdict(
                blocked=True,
                reason=(
                    f"Identical call to '{tool_name}' repeated "
                    f"{self._call_counts[call_hash]} times "
                    f"(max {self._config.max_identical_calls})."
                ),
            )

        # 2. Per-tool budget (polling tools)
        self._per_tool_counts[tool_name] = self._per_tool_counts.get(tool_name, 0) + 1
        if self._per_tool_counts[tool_name] > self._config.poll_tool_budget:
            self._emit_triggered("poll_budget", tool_name)
            return LoopVerdict(
                blocked=True,
                reason=(
                    f"Tool '{tool_name}' exceeded poll budget "
                    f"({self._config.poll_tool_budget})."
                ),
            )

        # 3. Ping-pong detection
        self._tool_sequence.append(tool_name)
        if len(self._tool_sequence) >= self._config.ping_pong_window:
            if self._detect_ping_pong():
                self._emit_triggered("ping_pong", tool_name)
                return LoopVerdict(
                    blocked=True,
                    reason="Repetitive tool-calling pattern detected (ping-pong).",
                )

        return LoopVerdict()

    def check_response(self, content: str) -> LoopVerdict:
        """Check whether an agent response indicates a loop. Reserved for future use."""
        return LoopVerdict()

    # ------------------------------------------------------------------
    # Context compression — token-aware (Phase 21)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_system(msg: object) -> bool:
        """Check if a message has role == system."""
        return getattr(msg, 'role', None) == 'system'

    @staticmethod
    def _is_tool(msg: object) -> bool:
        """Check if a message has role == tool."""
        return getattr(msg, 'role', None) == 'tool'

    def set_budget(self, budget: object) -> None:
        """Set the BATS UnifiedBudget for BACM-aware compression (Phase 31)."""
        self._budget = budget

    def compress_context(self, messages: list) -> list:
        """Apply context overflow recovery to message list.

        When BACM_COMPRESSION is enabled and a budget is set, uses BACM-lite
        importance-scored compression before falling back to structural stages.

        When ``ANATOMY_AGENT_PERMISSIONS`` is enabled, uses **token-count**
        thresholds derived from the model's context window:

        - 80 % of window → graduated compression (stages 1-3)
        - 95 % of window → hard truncation (stage 4)

        When the flag is disabled, falls back to the legacy message-count
        threshold for backwards compatibility.
        """
        # Phase 31: Try BACM first if budget is available
        if self._is_bacm_enabled() and self._budget is not None:
            bacm_result = self._bacm_compress(messages)
            if bacm_result is not None:
                return bacm_result

        if _is_token_context_enabled():
            return self._compress_context_tokens(messages)
        return self._compress_context_legacy(messages)

    @staticmethod
    def _is_bacm_enabled() -> bool:
        return os.environ.get("BACM_COMPRESSION", "true").lower() in ("true", "1", "yes")

    def _bacm_compress(self, messages: list) -> list | None:
        """Apply BACM budget-aware compression. Returns None if no compression needed."""
        try:
            from openjarvis.sessions.compression import BACMCompressor

            if self._bacm is None:
                self._bacm = BACMCompressor()

            # Calculate budget_ratio from token counts
            token_count = _estimate_tokens(messages)
            budget_ratio = 1.0 - (token_count / max(self._context_window, 1))
            budget_ratio = max(0.0, min(1.0, budget_ratio))

            # Also consider BATS budget regime if available
            if hasattr(self._budget, "remaining_pct"):
                bats_ratio = self._budget.remaining_pct / 100.0
                # Use the more aggressive (lower) ratio
                budget_ratio = min(budget_ratio, bats_ratio)

            compressed = self._bacm.compress_messages(
                messages,
                budget_ratio,
                current_task=self._config.current_task,
            )
            if len(compressed) < len(messages):
                logger.debug(
                    "BACM compressed %d -> %d messages (budget_ratio=%.2f)",
                    len(messages), len(compressed), budget_ratio,
                )
                return compressed
        except (ImportError, OSError, ValueError, TypeError) as exc:
            logger.debug("BACM compression failed (falling through): %s", exc)

        return None

    # -- Token-aware path (Phase 21) ------------------------------------

    def _compress_context_tokens(self, messages: list) -> list:
        """Token-aware compression with graduated stages."""
        token_count = _estimate_tokens(messages)

        if token_count <= self._compress_limit:
            return messages

        # Stage 1: Truncate old tool result messages
        compressed = self._stage1_truncate_tool_results(messages)
        if _estimate_tokens(compressed) <= self._compress_limit:
            return compressed

        # Stage 1.5 (Layer 2): LLM summarization of oldest non-system messages.
        # Before hard structural changes, attempt to preserve semantic meaning
        # by summarising the oldest messages via a cheap Haiku call.
        compressed = self._stage1_5_llm_summarize(compressed)
        if _estimate_tokens(compressed) <= self._compress_limit:
            return compressed

        # Stage 2: Sliding window — keep system + recent
        compressed = self._stage2_sliding_window(compressed)
        if _estimate_tokens(compressed) <= self._compress_limit:
            return compressed

        # Stage 3: Drop tool call/result pairs from middle
        compressed = self._stage3_drop_middle_pairs(compressed)
        if _estimate_tokens(compressed) <= self._truncate_limit:
            return compressed

        # Stage 4 (hard truncation): system + last 4 non-system messages
        return self._stage4_hard_truncate(compressed)

    # -- Legacy path (message-count) ------------------------------------

    def _compress_context_legacy(self, messages: list) -> list:
        """Original message-count-based compression (pre-Phase 21)."""
        if len(messages) <= self._config.max_context_messages:
            return messages

        compressed = self._stage1_truncate_tool_results(messages)
        if len(compressed) <= self._config.max_context_messages:
            return compressed

        compressed = self._stage2_sliding_window(compressed)
        if len(compressed) <= self._config.max_context_messages:
            return compressed

        compressed = self._stage3_drop_middle_pairs(compressed)
        if len(compressed) <= self._config.max_context_messages:
            return compressed

        return self._stage4_hard_truncate(compressed)

    # -- Layer 2: LLM-based summarization (Phase 21, Task 21-07) ---------

    def _stage1_5_llm_summarize(self, messages: list) -> list:
        """Replace the oldest N non-system messages with an LLM-generated summary.

        Uses :func:`shared.context_summarizer.summarize_context` (Haiku) to
        condense semantic meaning before resorting to structural truncation.
        If summarization fails or is disabled, returns messages unchanged
        (fail-open).
        """
        from shared.context_summarizer import is_enabled as _summarizer_enabled

        if not _summarizer_enabled():
            return messages

        # Identify non-system messages eligible for summarization.
        # Summarize the oldest half, keep the most recent half intact.
        system_msgs = [m for m in messages if self._is_system(m)]
        non_system = [m for m in messages if not self._is_system(m)]

        if len(non_system) < 4:
            return messages  # too few to summarize

        split = len(non_system) // 2
        old_msgs = non_system[:split]
        recent_msgs = non_system[split:]

        # Convert message objects to dicts for the summarizer.
        old_dicts = []
        for m in old_msgs:
            old_dicts.append({
                "role": getattr(m, "role", "unknown"),
                "content": getattr(m, "content", ""),
            })

        # Run the async summarizer from a sync context.
        try:
            from shared.context_summarizer import summarize_context

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    summary = pool.submit(
                        asyncio.run, summarize_context(old_dicts)
                    ).result(timeout=10)
            else:
                summary = loop.run_until_complete(summarize_context(old_dicts))
        except (OSError, RuntimeError, ValueError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("LLM summarization skipped: %s", e)
            return messages

        if not summary:
            return messages

        # Create a single summary message to replace the old messages.
        from openjarvis.core.types import Message, Role

        summary_msg = Message(
            role=Role.SYSTEM,
            content=f"[Context summary of {len(old_msgs)} earlier messages]\n{summary}",
        )
        return system_msgs + [summary_msg] + recent_msgs

    # -- Shared compression stages --------------------------------------

    def _stage1_truncate_tool_results(self, messages: list) -> list:
        """Stage 1: Replace old tool result contents with a placeholder."""
        threshold = len(messages) // 2
        compressed = []
        for i, msg in enumerate(messages):
            if i < threshold and self._is_tool(msg):
                from openjarvis.core.types import Message, Role
                compressed.append(Message(
                    role=Role.TOOL,
                    content="[Tool result truncated]",
                    tool_call_id=getattr(msg, 'tool_call_id', None),
                    name=getattr(msg, 'name', None),
                ))
            else:
                compressed.append(msg)
        return compressed

    def _stage2_sliding_window(self, messages: list) -> list:
        """Stage 2: Keep system messages + a sliding window of recent."""
        system_msgs = [m for m in messages if self._is_system(m)]
        non_system = [m for m in messages if not self._is_system(m)]
        window_size = self._config.max_context_messages - len(system_msgs)
        if len(non_system) > window_size:
            non_system = non_system[-window_size:]
        return system_msgs + non_system

    def _stage3_drop_middle_pairs(self, messages: list) -> list:
        """Stage 3: Drop tool call/result pairs from the middle."""
        system_msgs = [m for m in messages if self._is_system(m)]
        keep_start = max(len(system_msgs), len(messages) // 10)
        keep_end = len(messages) // 2
        return messages[:keep_start] + messages[-keep_end:]

    @staticmethod
    def _stage4_hard_truncate(messages: list) -> list:
        """Stage 4: Extreme — system messages + last 4 non-system."""
        sys_final = [m for m in messages if getattr(m, 'role', None) == 'system']
        tail = [m for m in messages if getattr(m, 'role', None) != 'system']
        return sys_final + tail[-4:]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all tracking state — always via Rust backend."""
        self._call_counts.clear()
        self._tool_sequence.clear()
        self._per_tool_counts.clear()
        self._warned_cycles.clear()
        if self._rust_impl is not None:
            self._rust_impl.reset()

    def _detect_ping_pong(self) -> bool:
        """Detect repeating patterns in tool call sequence."""
        seq = list(self._tool_sequence)
        n = len(seq)
        # Check for period-2 pattern (A-B-A-B)
        for period in (2, 3):
            if n >= period * 2:
                tail = seq[-period * 2:]
                pattern = tail[:period]
                if all(tail[i] == pattern[i % period] for i in range(len(tail))):
                    return True
        return False

    def _emit_triggered(self, reason_type: str, tool_name: str) -> None:
        """Publish a LOOP_GUARD_TRIGGERED event."""
        if self._bus:
            self._bus.publish(
                EventType.LOOP_GUARD_TRIGGERED,
                {"reason_type": reason_type, "tool": tool_name},
            )


__all__ = [
    "LoopGuard",
    "LoopGuardConfig",
    "LoopVerdict",
    "MODEL_CONTEXT_WINDOWS",
    "_estimate_tokens",
]
