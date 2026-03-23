"""Loop guard — detect and prevent degenerate task loops in daemons.

Ported from OpenJarvis agents/loop_guard.py. Simplified for Perseus daemons:
no Rust backend, no EventBus dependency.

Usage in a daemon tick loop:
    guard = LoopGuard()

    # Before executing a task:
    verdict = guard.check(task_type, json.dumps(payload))
    if verdict.blocked:
        logger.warning("Loop guard blocked: %s", verdict.reason)
        await fail_task(task_id, f"Loop guard: {verdict.reason}")
        continue

    # After a successful tick:
    guard.tick()  # resets per-tick counters
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass


@dataclass
class LoopVerdict:
    blocked: bool = False
    reason: str = ""


@dataclass
class LoopGuardConfig:
    max_identical_calls: int = 3       # same (task_type, payload) hash
    ping_pong_window: int = 6          # detect A-B-A-B cycling
    max_same_task_per_tick: int = 5    # same task_type in one tick


class LoopGuard:
    """Detect degenerate loops in daemon task processing."""

    def __init__(self, config: LoopGuardConfig | None = None):
        self._config = config or LoopGuardConfig()
        self._call_counts: dict[str, int] = {}
        self._task_sequence: deque[str] = deque(maxlen=self._config.ping_pong_window * 2)
        self._per_tick_counts: dict[str, int] = {}

    def check(self, task_type: str, payload_str: str = "") -> LoopVerdict:
        """Check whether a task should proceed or be blocked."""

        # 1. Identical call detection
        call_hash = hashlib.sha256(f"{task_type}:{payload_str}".encode()).hexdigest()[:16]
        self._call_counts[call_hash] = self._call_counts.get(call_hash, 0) + 1
        if self._call_counts[call_hash] > self._config.max_identical_calls:
            return LoopVerdict(
                blocked=True,
                reason=f"Identical task '{task_type}' repeated {self._call_counts[call_hash]}x (max {self._config.max_identical_calls})",
            )

        # 2. Per-tick budget
        self._per_tick_counts[task_type] = self._per_tick_counts.get(task_type, 0) + 1
        if self._per_tick_counts[task_type] > self._config.max_same_task_per_tick:
            return LoopVerdict(
                blocked=True,
                reason=f"Task '{task_type}' exceeded per-tick budget ({self._config.max_same_task_per_tick})",
            )

        # 3. Ping-pong detection
        self._task_sequence.append(task_type)
        if len(self._task_sequence) >= self._config.ping_pong_window:
            if self._detect_ping_pong():
                return LoopVerdict(
                    blocked=True,
                    reason="Repetitive task pattern detected (ping-pong cycling)",
                )

        return LoopVerdict()

    def tick(self):
        """Call at the end of each daemon tick to reset per-tick counters."""
        self._per_tick_counts.clear()

    def reset(self):
        """Full reset of all tracking state."""
        self._call_counts.clear()
        self._task_sequence.clear()
        self._per_tick_counts.clear()

    def _detect_ping_pong(self) -> bool:
        seq = list(self._task_sequence)
        n = len(seq)
        for period in (2, 3):
            if n >= period * 2:
                tail = seq[-period * 2:]
                pattern = tail[:period]
                if all(tail[i] == pattern[i % period] for i in range(len(tail))):
                    return True
        return False
