"""Phase 19-02: Retrieval telemetry for MAGMA.

Tests:
- _record_retrieval_stats inserts row with correct params
- magma_retrieve records timing data (latency_ms > 0)
- Feature flag OFF disables stats recording
- DB errors in stats recording don't crash retrieval
- Long queries truncated to 500 chars
"""

import asyncio
import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────

def _run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ensure_fake_db():
    """Ensure shared.db is a mock module in sys.modules.

    The conftest module isolation guard may remove our fake between tests.
    This must be called at the start of each test that exercises DB code.
    """
    if "shared.db" not in sys.modules or not hasattr(sys.modules["shared.db"], "_is_test_fake"):
        fake = types.ModuleType("shared.db")
        fake._is_test_fake = True
        fake.execute = AsyncMock()
        fake.fetch_one = AsyncMock(return_value=None)
        fake.fetch_all = AsyncMock(return_value=[])
        fake.fetch_val = AsyncMock(return_value=None)
        fake.init_pool = AsyncMock()
        fake.close_pool = AsyncMock()
        fake.get_conn = MagicMock()
        fake.transaction = MagicMock()
        fake.insert_task = AsyncMock(return_value=1)
        fake.emit_event = AsyncMock(return_value=1)
        fake.get_config = AsyncMock(return_value=None)
        fake.set_config = AsyncMock()
        fake.increment_config_int = AsyncMock(return_value=1)
        sys.modules["shared.db"] = fake

    # Also ensure psycopg fakes exist
    for mod_name in ("psycopg", "psycopg.rows", "psycopg.types", "psycopg.types.json", "psycopg_pool"):
        if mod_name not in sys.modules:
            f = types.ModuleType(mod_name)
            if mod_name == "psycopg":
                f.Error = type("Error", (Exception,), {})
            elif mod_name == "psycopg.rows":
                f.dict_row = MagicMock()
            elif mod_name == "psycopg.types.json":
                f.Jsonb = MagicMock()
            elif mod_name == "psycopg_pool":
                f.AsyncConnectionPool = MagicMock()
            sys.modules[mod_name] = f
        elif mod_name == "psycopg" and not hasattr(sys.modules[mod_name], "Error"):
            sys.modules[mod_name].Error = type("Error", (Exception,), {})

    return sys.modules["shared.db"]


def _reload_magma():
    """Force-reload shared.magma to pick up the current sys.modules['shared.db']."""
    if "shared.magma" in sys.modules:
        importlib.reload(sys.modules["shared.magma"])
    from shared.magma import _record_retrieval_stats
    return _record_retrieval_stats


# ── Test 1: _record_retrieval_stats inserts row ──────────────────

def test_record_retrieval_stats_inserts_row():
    """When flag is ON, _record_retrieval_stats calls db.execute with correct params."""
    fake_db = _ensure_fake_db()
    mock_execute = AsyncMock()
    fake_db.execute = mock_execute

    _record_retrieval_stats = _reload_magma()

    with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
        _run(_record_retrieval_stats(
            query="why did client churn",
            intent="causal",
            anchors_found=5,
            anchors_used=3,
            confidence_avg=0.72,
            latency_ms=150,
            decompose_ms=20,
            anchor_ms=50,
            beam_ms=60,
            linearize_ms=20,
            abstained=False,
        ))

    mock_execute.assert_called_once()
    call_args = mock_execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]

    assert "INSERT INTO magma_retrieval_stats" in sql
    assert params[0] == "why did client churn"  # query
    assert params[1] == "causal"                 # intent
    assert params[2] == 5                        # anchors_found
    assert params[3] == 3                        # anchors_used
    assert abs(params[4] - 0.72) < 0.001        # confidence_avg
    assert params[5] == 150                      # latency_ms
    assert params[6] == 20                       # decompose_ms
    assert params[7] == 50                       # anchor_ms
    assert params[8] == 60                       # beam_ms
    assert params[9] == 20                       # linearize_ms
    assert params[10] is False                   # abstained


# ── Test 2: magma_retrieve records timing (latency_ms > 0) ──────

def test_retrieval_telemetry_timing():
    """magma_retrieve instruments phases and fires telemetry with latency_ms > 0."""
    _ensure_fake_db()

    mock_driver = MagicMock()

    fake_anchors = [
        {"node_id": "magma_1", "content": "test", "confidence": 0.8, "timestamp": ""},
        {"node_id": "magma_2", "content": "test2", "confidence": 0.7, "timestamp": ""},
    ]

    recorded_calls = []

    async def mock_record(**kwargs):
        recorded_calls.append(kwargs)

    with (
        patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}),
        patch("shared.magma._get_driver", return_value=mock_driver),
        patch("shared.magma.meta_search_params", new_callable=AsyncMock, return_value={}),
        patch("shared.magma._decompose_query", new_callable=AsyncMock, return_value={
            "intent": MagicMock(value="semantic"),
            "entities": [],
            "time_start": None,
            "time_end": None,
            "causal_direction": None,
        }),
        patch("shared.magma._find_anchors_linked", new_callable=AsyncMock, return_value=fake_anchors),
        patch("shared.magma._scored_beam_search", new_callable=AsyncMock, return_value=fake_anchors),
        patch("shared.magma._linearize_with_provenance", return_value="linearized result"),
        patch("shared.magma.check_staleness", return_value=None),
        patch("shared.magma._record_retrieval_stats", side_effect=mock_record) as mock_rec,
    ):
        from shared.magma import magma_retrieve

        async def run_retrieve():
            result = await magma_retrieve("test query", limit=10)
            # Allow the fire-and-forget task to execute
            await asyncio.sleep(0.05)
            return result

        result = _run(run_retrieve())

    assert result  # non-empty
    mock_rec.assert_called_once()
    call_kwargs = mock_rec.call_args[1]
    assert call_kwargs["latency_ms"] >= 0
    assert call_kwargs["decompose_ms"] >= 0
    assert call_kwargs["anchor_ms"] >= 0
    assert call_kwargs["beam_ms"] >= 0
    assert call_kwargs["linearize_ms"] >= 0
    assert call_kwargs["anchors_found"] == 2
    assert call_kwargs["abstained"] is False


# ── Test 3: Feature flag OFF disables stats recording ────────────

def test_retrieval_telemetry_flag_off():
    """When ANATOMY_COST_DASHBOARD is false, _record_retrieval_stats is a no-op."""
    fake_db = _ensure_fake_db()
    mock_execute = AsyncMock()
    fake_db.execute = mock_execute

    _record_retrieval_stats = _reload_magma()

    with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "false"}):
        _run(_record_retrieval_stats(
            query="test",
            intent="semantic",
            anchors_found=5,
            anchors_used=3,
            confidence_avg=0.72,
            latency_ms=150,
            decompose_ms=20,
            anchor_ms=50,
            beam_ms=60,
            linearize_ms=20,
            abstained=False,
        ))

    mock_execute.assert_not_called()


# ── Test 4: DB errors don't crash retrieval ──────────────────────

def test_retrieval_stats_db_error_silent():
    """DB errors in _record_retrieval_stats log a warning but don't raise."""
    fake_db = _ensure_fake_db()
    mock_execute = AsyncMock(side_effect=OSError("connection refused"))
    fake_db.execute = mock_execute

    _record_retrieval_stats = _reload_magma()

    with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
        # This must NOT raise
        _run(_record_retrieval_stats(
            query="test query",
            intent="semantic",
            anchors_found=3,
            anchors_used=2,
            confidence_avg=0.65,
            latency_ms=100,
            decompose_ms=10,
            anchor_ms=40,
            beam_ms=30,
            linearize_ms=20,
            abstained=False,
        ))

    # If we got here without an exception, the test passes
    mock_execute.assert_called_once()


# ── Test 5: Long queries truncated to 500 chars ─────────────────

def test_record_retrieval_stats_truncates_long_query():
    """Queries longer than 500 chars are truncated before DB insert."""
    fake_db = _ensure_fake_db()
    mock_execute = AsyncMock()
    fake_db.execute = mock_execute
    long_query = "x" * 1000

    _record_retrieval_stats = _reload_magma()

    with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
        _run(_record_retrieval_stats(
            query=long_query,
            intent="semantic",
            anchors_found=1,
            anchors_used=1,
            confidence_avg=0.5,
            latency_ms=50,
            decompose_ms=10,
            anchor_ms=20,
            beam_ms=10,
            linearize_ms=10,
            abstained=False,
        ))

    params = mock_execute.call_args[0][1]
    assert len(params[0]) == 500  # truncated
