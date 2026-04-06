"""Tests for Phase 24 tasks: BoundedUUIDSet dedup (24-08) and broadcast sender exclusion (24-07)."""

import asyncio
import threading
from unittest.mock import AsyncMock

from shared.dedup import BoundedUUIDSet

# ── BoundedUUIDSet tests ───────────────────────────────────────────


def test_bounded_set_add_new():
    """add() returns True for a new (unseen) ID."""
    s = BoundedUUIDSet(capacity=10)
    assert s.add("msg-001") is True


def test_bounded_set_add_duplicate():
    """add() returns False for an already-seen ID."""
    s = BoundedUUIDSet(capacity=10)
    s.add("msg-001")
    assert s.add("msg-001") is False


def test_bounded_set_capacity_eviction():
    """Oldest entries are evicted when capacity is exceeded."""
    s = BoundedUUIDSet(capacity=3)
    s.add("a")
    s.add("b")
    s.add("c")
    # At capacity — adding one more should evict "a"
    s.add("d")
    assert "a" not in s, "oldest entry should have been evicted"
    assert "b" in s
    assert "c" in s
    assert "d" in s
    assert len(s) == 3


def test_bounded_set_contains():
    """__contains__ works for present and absent IDs."""
    s = BoundedUUIDSet(capacity=10)
    s.add("present")
    assert "present" in s
    assert "absent" not in s


def test_bounded_set_clear():
    """clear() empties the set."""
    s = BoundedUUIDSet(capacity=10)
    s.add("x")
    s.add("y")
    s.clear()
    assert len(s) == 0
    assert "x" not in s


def test_bounded_set_move_to_end_on_duplicate():
    """Accessing a duplicate moves it to the end, preventing premature eviction."""
    s = BoundedUUIDSet(capacity=3)
    s.add("a")
    s.add("b")
    s.add("c")
    # Re-add "a" — should move to end (most recent)
    s.add("a")
    # Now add "d" — should evict "b" (oldest), not "a"
    s.add("d")
    assert "a" in s, "re-added entry should survive eviction"
    assert "b" not in s, "oldest untouched entry should be evicted"
    assert "c" in s
    assert "d" in s


def test_bounded_set_thread_safe():
    """Concurrent adds from multiple threads do not corrupt the set."""
    s = BoundedUUIDSet(capacity=5000)
    ids_per_thread = 200
    num_threads = 10
    results: list[bool] = []
    lock = threading.Lock()

    def worker(thread_id: int):
        local_results = []
        for i in range(ids_per_thread):
            result = s.add(f"t{thread_id}-{i}")
            local_results.append(result)
        with lock:
            results.extend(local_results)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_unique = num_threads * ids_per_thread
    assert len(results) == total_unique
    # All IDs are unique across threads, so all should return True
    assert all(results), "All unique IDs should return True"
    assert len(s) == total_unique


def test_bounded_set_thread_safe_duplicates():
    """Concurrent duplicate adds are correctly detected."""
    s = BoundedUUIDSet(capacity=5000)
    shared_id = "shared-message-id"
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        result = s.add(shared_id)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should see True (new), rest see False (duplicate)
    assert results.count(True) == 1
    assert results.count(False) == 19


# ── Broadcast sender exclusion tests ──────────────────────────────
#
# shared.comms imports shared.db which requires psycopg (not available
# in the test runner).  We pre-seed sys.modules with stubs so the import
# succeeds, then patch the relevant functions.

import sys  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

# Ensure shared.db and shared.observability are importable without psycopg
_mock_db = MagicMock()
_mock_db.emit_event = AsyncMock()
_mock_observability = MagicMock()
_mock_observability.enrich_payload_with_context = lambda p, **kw: p

_modules_to_stub = {
    "psycopg": MagicMock(),
    "psycopg.rows": MagicMock(),
    "shared.db": _mock_db,
    "shared.observability": _mock_observability,
}

for mod_name, mod_mock in _modules_to_stub.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = mod_mock

# Now we can safely import shared.comms
import importlib  # noqa: E402

if "shared.comms" in sys.modules:
    importlib.reload(sys.modules["shared.comms"])
from shared.comms import broadcast, send_alert  # noqa: E402, I001


_comms_mod = sys.modules["shared.comms"]


def _reset_mock_db():
    """Reset the mock emit_event between tests."""
    _mock_db.emit_event = AsyncMock()
    # Also patch the module-level reference in comms
    _comms_mod.db = _mock_db


def test_broadcast_excludes_sender():
    """When exclude_sender is provided, _exclude_sender is set in the payload."""
    _reset_mock_db()

    async def _run():
        await broadcast(
            "test_event",
            {"data": "value"},
            sender="titan",
            exclude_sender="titan",
        )

        _mock_db.emit_event.assert_called_once()
        call_args = _mock_db.emit_event.call_args
        event_type = call_args[0][0]
        payload = call_args[0][1]

        assert event_type == "test_event"
        assert payload["sender"] == "titan"
        assert payload["_exclude_sender"] == "titan"
        assert payload["data"] == "value"

    asyncio.run(_run())


def test_broadcast_without_exclusion():
    """When exclude_sender is not provided, _exclude_sender is absent from payload (backward compat)."""
    _reset_mock_db()

    async def _run():
        await broadcast(
            "test_event",
            {"data": "value"},
            sender="hermes",
        )

        _mock_db.emit_event.assert_called_once()
        call_args = _mock_db.emit_event.call_args
        payload = call_args[0][1]

        assert payload["sender"] == "hermes"
        assert "_exclude_sender" not in payload
        assert payload["data"] == "value"

    asyncio.run(_run())


def test_broadcast_exclude_sender_empty_string_ignored():
    """Empty string for exclude_sender is treated as no exclusion."""
    _reset_mock_db()

    async def _run():
        await broadcast(
            "test_event",
            sender="perseus",
            exclude_sender="",
        )

        payload = _mock_db.emit_event.call_args[0][1]
        assert "_exclude_sender" not in payload

    asyncio.run(_run())


def test_send_alert_excludes_sender():
    """send_alert() passes exclude_sender when sender is provided."""
    _reset_mock_db()

    async def _run():
        await send_alert("something broke", sender="titan")

        payload = _mock_db.emit_event.call_args[0][1]
        assert payload["_exclude_sender"] == "titan"

    asyncio.run(_run())


def test_send_alert_no_sender_no_exclusion():
    """send_alert() without sender does not set _exclude_sender."""
    _reset_mock_db()

    async def _run():
        await send_alert("something broke")

        payload = _mock_db.emit_event.call_args[0][1]
        assert "_exclude_sender" not in payload

    asyncio.run(_run())


# ── send_protocol_message tests ──────────────────────────────────────

from shared.comms import send_protocol_message  # noqa: E402


def test_send_protocol_message_fallback_to_broadcast():
    """When A2A is unavailable, send_protocol_message falls back to broadcast."""
    _reset_mock_db()
    from shared.protocols import ProtocolMessage, ProtocolType

    msg = ProtocolMessage(
        type=ProtocolType.SHUTDOWN_REQUEST,
        sender="orchestrator",
        recipient="titan",
        payload={"reason": "test"},
    )

    async def _run():
        # Patch _USE_A2A to False to force fallback
        import shared.comms as comms_mod
        original = comms_mod._USE_A2A
        comms_mod._USE_A2A = False
        try:
            result = await send_protocol_message(msg)
        finally:
            comms_mod._USE_A2A = original

        # Should have broadcast as fallback
        assert result is None
        _mock_db.emit_event.assert_called_once()
        call_args = _mock_db.emit_event.call_args
        assert call_args[0][0] == "protocol_shutdown_request"
        payload = call_args[0][1]
        assert payload["sender"] == "orchestrator"
        assert payload["recipient"] == "titan"

    asyncio.run(_run())


def test_send_protocol_message_a2a_failure_falls_back():
    """When A2A call fails, send_protocol_message falls back to broadcast."""
    _reset_mock_db()
    from shared.protocols import ProtocolMessage, ProtocolType

    msg = ProtocolMessage(
        type=ProtocolType.HEALTH_CHECK,
        sender="orchestrator",
        recipient="hermes",
    )

    async def _run():
        # Force A2A path but it will fail (no running agent), should fall back to broadcast
        result = await send_protocol_message(msg)
        # Should have broadcast as fallback (via emit_event)
        assert result is None
        _mock_db.emit_event.assert_called_once()
        call_args = _mock_db.emit_event.call_args
        assert call_args[0][0] == "protocol_health_check"

    asyncio.run(_run())
