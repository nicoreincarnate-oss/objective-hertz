"""Tests for Session Health (Phase 12: FP-05)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from shared.daemon_memory import WorkingMemory


def test_token_accumulation():
    """record_tokens(1000) increments total_tokens and updates saturation."""
    mem = WorkingMemory()
    assert mem.total_tokens == 0
    assert mem.context_saturation_pct == 0.0

    mem.record_tokens(1000)
    assert mem.total_tokens == 1000
    assert mem.context_saturation_pct == pytest.approx(0.05, abs=0.01)

    mem.record_tokens(500)
    assert mem.total_tokens == 1500


def test_needs_reset_at_2m_tokens():
    """Returns True when total_tokens >= 2,000,000."""
    mem = WorkingMemory()
    mem.total_tokens = 1_999_999
    assert not mem.needs_reset()

    mem.total_tokens = 2_000_000
    assert mem.needs_reset()


def test_needs_reset_at_72h():
    """Returns True when elapsed >= 72 hours."""
    mem = WorkingMemory()
    # Simulate session started 73 hours ago
    mem._session_start = time.time() - (73 * 3600)
    assert mem.needs_reset()


def test_reset_clears_all_counters():
    """reset() zeros everything and clears OrderedDict."""
    mem = WorkingMemory()
    mem.set("key1", "val1")
    mem.set("key2", "val2")
    mem.record_tokens(5000)
    mem.record_error()
    mem.record_state_transition()

    assert len(mem) == 2
    assert mem.total_tokens == 5000
    assert mem.error_count == 1
    assert mem.state_transitions == 1

    mem.reset()

    assert len(mem) == 0
    assert mem.total_tokens == 0
    assert mem.error_count == 0
    assert mem.state_transitions == 0
    assert mem.context_saturation_pct == 0.0
    assert mem.elapsed_seconds == 0.0


def test_health_snapshot_format():
    """Snapshot dict contains all expected keys with correct types."""
    mem = WorkingMemory()
    mem.record_tokens(100)
    mem.record_error()
    mem.record_state_transition()
    mem.set("item", "value")

    snap = mem.health_snapshot()

    assert isinstance(snap["total_tokens"], int)
    assert snap["total_tokens"] == 100
    assert isinstance(snap["elapsed_seconds"], float)
    assert isinstance(snap["error_count"], int)
    assert snap["error_count"] == 1
    assert isinstance(snap["state_transitions"], int)
    assert snap["state_transitions"] == 1
    assert isinstance(snap["context_saturation_pct"], float)
    assert isinstance(snap["needs_reset"], bool)
    assert snap["needs_reset"] is False
    assert isinstance(snap["items_count"], int)
    assert snap["items_count"] == 1
