"""Tests for Phase 24: re-entrant task execution (A-22) and structured protocols (D-15).

Re-entrant tasks:
- test_reentrant_task_requeues: needs_more_work sentinel triggers re-queue
- test_reentrant_depth_limit: depth >= max_depth fails with depth_exceeded
- test_reentrant_depth_increments: each re-queue increments depth

Structured protocols:
- test_protocol_message_roundtrip: to_dict/from_dict roundtrip
- test_protocol_types_complete: all 7 types exist
- test_is_protocol_message_detects: correctly identifies protocol messages
- test_non_protocol_message_rejected: regular dict returns False
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Pre-stub psycopg and friends so `shared.db` can be imported ────────
# psycopg is not available in the test runner; these stubs let the module
# load without error.  The re-entrant tests mock the functions they call.
import importlib

for _mod in ("psycopg", "psycopg.rows", "psycopg_pool"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# shared.config and shared.observability may also need stubs
if "shared.config" not in sys.modules or isinstance(sys.modules["shared.config"], MagicMock):
    _cfg = MagicMock()
    _cfg.config = MagicMock()
    sys.modules["shared.config"] = _cfg
if "shared.observability" not in sys.modules or isinstance(sys.modules["shared.observability"], MagicMock):
    _obs = MagicMock()
    _obs.capture_exception = lambda *a, **k: None
    _obs.enrich_payload_with_context = lambda p, **k: p
    _obs.observe_db_query = MagicMock()
    sys.modules["shared.observability"] = _obs

def _ensure_real_shared_db():
    """Ensure shared.db is the real module (not a stub) with REENTRANT_SENTINEL."""
    if "shared.db" not in sys.modules or not hasattr(sys.modules["shared.db"], "REENTRANT_SENTINEL"):
        sys.modules.pop("shared.db", None)
        import shared.db  # noqa: F811
        importlib.reload(shared.db)

# ── Protocol tests (no DB needed) ──────────────────────────────────────


def test_protocol_types_complete():
    """All 10 protocol types must exist."""
    from shared.protocols import ProtocolType

    expected = {
        "shutdown_request",
        "shutdown_response",
        "escalation",
        "delegation_request",
        "delegation_result",
        "health_check",
        "health_response",
        "synthesis_instruction",
        "heartbeat_request",
        "heartbeat_response",
    }
    actual = {pt.value for pt in ProtocolType}
    assert actual == expected, f"Missing types: {expected - actual}"


def test_protocol_payload_schemas_keys_match():
    """PROTOCOL_PAYLOAD_SCHEMAS keys must be a subset of ProtocolType values."""
    from shared.protocols import PROTOCOL_PAYLOAD_SCHEMAS, ProtocolType

    for key in PROTOCOL_PAYLOAD_SCHEMAS:
        assert isinstance(key, ProtocolType), f"Key {key} is not a ProtocolType"


def test_should_process_event_self_excluded():
    """should_process_event returns False when sender == self."""
    from shared.protocols import should_process_event

    assert should_process_event({"sender": "titan"}, "titan") is False


def test_should_process_event_different_sender():
    """should_process_event returns True when sender differs."""
    from shared.protocols import should_process_event

    assert should_process_event({"sender": "hermes"}, "titan") is True


def test_should_process_event_no_sender():
    """should_process_event returns True when no sender field."""
    from shared.protocols import should_process_event

    assert should_process_event({}, "titan") is True
    assert should_process_event({"data": 1}, "titan") is True


def test_should_process_event_empty_sender():
    """should_process_event returns True when sender is empty string."""
    from shared.protocols import should_process_event

    assert should_process_event({"sender": ""}, "titan") is True


def test_should_process_event_exclude_sender_field():
    """should_process_event respects _exclude_sender field."""
    from shared.protocols import should_process_event

    assert should_process_event({"_exclude_sender": "titan"}, "titan") is False
    assert should_process_event({"_exclude_sender": "hermes"}, "titan") is True


def test_protocol_message_roundtrip():
    """to_dict/from_dict should produce identical messages."""
    from shared.protocols import ProtocolMessage, ProtocolType

    msg = ProtocolMessage(
        type=ProtocolType.DELEGATION_REQUEST,
        sender="titan",
        recipient="hermes",
        payload={"task": "send_alert", "priority": 1},
        correlation_id="abc-123",
        timestamp=1712000000.0,
    )
    serialized = msg.to_dict()
    restored = ProtocolMessage.from_dict(serialized)

    assert restored.type == msg.type
    assert restored.sender == msg.sender
    assert restored.recipient == msg.recipient
    assert restored.payload == msg.payload
    assert restored.correlation_id == msg.correlation_id
    assert restored.timestamp == msg.timestamp


def test_protocol_message_roundtrip_minimal():
    """Roundtrip with only required fields (defaults for optional)."""
    from shared.protocols import ProtocolMessage, ProtocolType

    msg = ProtocolMessage(
        type=ProtocolType.HEALTH_CHECK,
        sender="perseus",
        recipient="titan",
    )
    serialized = msg.to_dict()
    restored = ProtocolMessage.from_dict(serialized)

    assert restored.type == ProtocolType.HEALTH_CHECK
    assert restored.payload == {}
    assert restored.correlation_id == ""
    assert restored.timestamp == 0.0


def test_is_protocol_message_detects():
    """is_protocol_message should return True for valid protocol dicts."""
    from shared.protocols import ProtocolType, is_protocol_message

    for pt in ProtocolType:
        data = {
            "protocol_type": pt.value,
            "sender": "test",
            "recipient": "test",
        }
        assert is_protocol_message(data), f"Failed to detect {pt.value}"


def test_non_protocol_message_rejected():
    """Regular dicts should not be detected as protocol messages."""
    from shared.protocols import is_protocol_message

    assert not is_protocol_message({})
    assert not is_protocol_message({"type": "task", "sender": "x"})
    assert not is_protocol_message({"protocol_type": "unknown_garbage"})
    assert not is_protocol_message({"protocol_type": 42})
    # Not a dict at all
    assert not is_protocol_message("not a dict")  # type: ignore[arg-type]


# ── Re-entrant task tests (mock DB) ───────────────────────────────────


@pytest.mark.asyncio
async def test_reentrant_task_requeues():
    """needs_more_work sentinel should trigger immediate re-queue."""
    _ensure_real_shared_db()
    from shared.db import REENTRANT_SENTINEL, maybe_requeue_task

    handler_result = {
        REENTRANT_SENTINEL: True,
        "updated_payload": {"stage": "enrichment", "attempt": 2},
    }

    with (
        patch.dict(os.environ, {"ANATOMY_TASK_RESILIENCE": "true"}),
        patch("shared.db.insert_task", new_callable=AsyncMock, return_value=42) as mock_insert,
        patch("shared.db.execute", new_callable=AsyncMock),
    ):
        new_id = await maybe_requeue_task(
            task_id=10,
            task_type="lead_enrich",
            handler_result=handler_result,
            current_depth=1,
            max_depth=5,
            priority=3,
            goal_tag="goal-abc",
        )

    assert new_id == 42
    mock_insert.assert_called_once_with(
        task_type="lead_enrich",
        payload={"stage": "enrichment", "attempt": 2},
        priority=3,
        dedupe=False,
        depth=2,
        max_depth=5,
        goal_tag="goal-abc",
    )


@pytest.mark.asyncio
async def test_reentrant_depth_limit():
    """When depth >= max_depth, task should be failed with depth_exceeded."""
    _ensure_real_shared_db()
    from shared.db import REENTRANT_SENTINEL, maybe_requeue_task

    handler_result = {REENTRANT_SENTINEL: True, "updated_payload": {}}

    with (
        patch.dict(os.environ, {"ANATOMY_TASK_RESILIENCE": "true"}),
        patch("shared.db.insert_task", new_callable=AsyncMock) as mock_insert,
        patch("shared.db.execute", new_callable=AsyncMock) as mock_execute,
    ):
        new_id = await maybe_requeue_task(
            task_id=10,
            task_type="pipeline_stage",
            handler_result=handler_result,
            current_depth=4,
            max_depth=5,
        )

    # Should NOT re-queue
    assert new_id is None
    mock_insert.assert_not_called()

    # Should mark as failed with depth_exceeded
    mock_execute.assert_called_once()
    call_args = mock_execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "depth_exceeded" in sql
    assert "depth limit exceeded" in params[0]
    assert params[1] == 10  # task_id


@pytest.mark.asyncio
async def test_reentrant_depth_increments():
    """Each re-queue should increment depth by 1."""
    _ensure_real_shared_db()
    from shared.db import REENTRANT_SENTINEL, maybe_requeue_task

    depths_seen: list[int] = []

    async def capture_insert(**kwargs):
        depths_seen.append(kwargs["depth"])
        return 100 + kwargs["depth"]

    handler_result = {REENTRANT_SENTINEL: True, "updated_payload": {"step": "x"}}

    with (
        patch.dict(os.environ, {"ANATOMY_TASK_RESILIENCE": "true"}),
        patch("shared.db.insert_task", side_effect=capture_insert),
        patch("shared.db.execute", new_callable=AsyncMock),
    ):
        # Simulate 3 successive re-queues at depths 0, 1, 2
        for d in range(3):
            await maybe_requeue_task(
                task_id=d,
                task_type="test_type",
                handler_result=handler_result,
                current_depth=d,
                max_depth=5,
            )

    assert depths_seen == [1, 2, 3]


@pytest.mark.asyncio
async def test_reentrant_disabled_without_flag():
    """When ANATOMY_TASK_RESILIENCE is not set, re-queue should be a no-op."""
    _ensure_real_shared_db()
    from shared.db import REENTRANT_SENTINEL, maybe_requeue_task

    handler_result = {REENTRANT_SENTINEL: True, "updated_payload": {}}

    with (
        patch.dict(os.environ, {}, clear=False),
        patch("shared.db.insert_task", new_callable=AsyncMock) as mock_insert,
    ):
        # Ensure flag is not set
        os.environ.pop("ANATOMY_TASK_RESILIENCE", None)
        new_id = await maybe_requeue_task(
            task_id=1,
            task_type="test",
            handler_result=handler_result,
        )

    assert new_id is None
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reentrant_ignores_non_sentinel():
    """When handler returns a normal dict (no sentinel), no re-queue."""
    _ensure_real_shared_db()
    from shared.db import maybe_requeue_task

    with (
        patch.dict(os.environ, {"ANATOMY_TASK_RESILIENCE": "true"}),
        patch("shared.db.insert_task", new_callable=AsyncMock) as mock_insert,
    ):
        # Normal completion dict
        result = await maybe_requeue_task(
            task_id=1,
            task_type="test",
            handler_result={"status": "ok", "data": [1, 2, 3]},
        )

    assert result is None
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reentrant_ignores_non_dict():
    """When handler returns a non-dict (string, None, etc.), no re-queue."""
    _ensure_real_shared_db()
    from shared.db import maybe_requeue_task

    with (
        patch.dict(os.environ, {"ANATOMY_TASK_RESILIENCE": "true"}),
        patch("shared.db.insert_task", new_callable=AsyncMock) as mock_insert,
    ):
        for result in [None, "done", 42, True, []]:
            new_id = await maybe_requeue_task(
                task_id=1,
                task_type="test",
                handler_result=result,
            )
            assert new_id is None

    mock_insert.assert_not_called()


# ── TaskResult tests ─────────────────────────────────────────────────


def test_task_result_completed():
    """TaskResult.completed creates correct result."""
    from shared.task_results import TaskResult

    r = TaskResult.completed({"key": "value"})
    assert r.status == "completed"
    assert r.payload == {"key": "value"}
    assert r.needs_requeue is False


def test_task_result_failed():
    """TaskResult.failed creates correct result."""
    from shared.task_results import TaskResult

    r = TaskResult.failed("some error", {"partial": True})
    assert r.status == "failed"
    assert r.error == "some error"
    assert r.needs_requeue is False


def test_task_result_needs_more_work():
    """TaskResult.needs_more_work creates correct result."""
    from shared.task_results import TaskResult

    r = TaskResult.needs_more_work({"stage": "enrichment"}, priority=3)
    assert r.status == "needs_more_work"
    assert r.needs_requeue is True
    assert r.requeue_priority == 3


def test_task_result_to_sentinel_dict():
    """TaskResult.to_sentinel_dict bridges to REENTRANT_SENTINEL format."""
    from shared.task_results import TaskResult

    r = TaskResult.needs_more_work({"step": 2})
    d = r.to_sentinel_dict()
    assert d["needs_more_work"] is True
    assert d["updated_payload"] == {"step": 2}

    r2 = TaskResult.completed({"done": True})
    d2 = r2.to_sentinel_dict()
    assert "needs_more_work" not in d2
    assert d2["status"] == "completed"


def test_task_result_max_reentry_depth():
    """MAX_REENTRY_DEPTH is set to 10."""
    from shared.task_results import MAX_REENTRY_DEPTH

    assert MAX_REENTRY_DEPTH == 10


# ── BoundedUUIDSet seed_from_db tests ────────────────────────────────


def test_seed_from_db():
    """seed_from_db populates the set correctly."""
    from shared.dedup import BoundedUUIDSet

    s = BoundedUUIDSet(capacity=5)
    s.seed_from_db(["a", "b", "c"])
    assert "a" in s
    assert "b" in s
    assert "c" in s
    assert len(s) == 3


def test_seed_from_db_truncates_to_capacity():
    """seed_from_db only keeps capacity-worth of recent IDs."""
    from shared.dedup import BoundedUUIDSet

    s = BoundedUUIDSet(capacity=3)
    s.seed_from_db(["a", "b", "c", "d", "e"])
    # Should only keep the last 3 (most recent)
    assert len(s) == 3
    assert "c" in s
    assert "d" in s
    assert "e" in s


def test_bounded_set_capacity_property():
    """capacity property returns the configured capacity."""
    from shared.dedup import BoundedUUIDSet

    s = BoundedUUIDSet(capacity=42)
    assert s.capacity == 42


# ── CooperativeShutdownMixin tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_cooperative_shutdown_mixin():
    """CooperativeShutdownMixin sets flag and returns ack."""
    from shared.daemon_base import CooperativeShutdownMixin

    class TestDaemon(CooperativeShutdownMixin):
        pass

    d = TestDaemon()
    assert d.is_shutdown_requested is False

    result = await d.handle_shutdown_request({"reason": "test"})
    assert d.is_shutdown_requested is True
    assert result["acknowledged"] is True
    assert result["estimated_completion_seconds"] == 5.0
    assert result["current_task"] is None


@pytest.mark.asyncio
async def test_cooperative_shutdown_mixin_with_task():
    """CooperativeShutdownMixin gives more time when task is running."""
    from shared.daemon_base import CooperativeShutdownMixin

    class TestDaemon(CooperativeShutdownMixin):
        pass

    d = TestDaemon()
    d._current_task_description = "processing lead 42"

    result = await d.handle_shutdown_request({"reason": "orchestrator_shutdown"})
    assert result["estimated_completion_seconds"] == 15.0
    assert result["current_task"] == "processing lead 42"
