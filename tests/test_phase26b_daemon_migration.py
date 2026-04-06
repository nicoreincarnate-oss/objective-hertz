"""Tests for Phase 26b-05 and 26b-06: PerseusDaemon and Orchestrator generator migrations.

Phase 26b-05: PerseusDaemon.tick_generator() -- scheduler tick as async generator
Phase 26b-06: Orchestrator _run_loop() utility -- consumes async generators with logging
"""

from __future__ import annotations

import logging
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Aggressively stub out ALL heavy transitive dependencies before project
# imports.  This lets us import perseus.daemon and orchestrator without
# needing psycopg, aiohttp, uvicorn, etc.
# ---------------------------------------------------------------------------

_FAKES: dict[str, types.ModuleType] = {}


def _ensure_fake(name: str, attrs: dict | None = None) -> types.ModuleType:
    """Create and register a fake module if not already importable."""
    if name in sys.modules:
        mod = sys.modules[name]
        # Patch any missing attrs
        for k, v in (attrs or {}).items():
            if not hasattr(mod, k):
                setattr(mod, k, v)
        return mod
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    _FAKES[name] = mod
    return mod


# -- psycopg family --
_ensure_fake("psycopg")
_ensure_fake("psycopg.rows", {"dict_row": MagicMock()})
_ensure_fake("psycopg_pool", {"AsyncConnectionPool": MagicMock()})

# -- dotenv --
_ensure_fake("dotenv", {"load_dotenv": lambda *a, **kw: None})

# -- shared.observability (must have every name any file imports) --
_ensure_fake("shared.observability", {
    "capture_exception": lambda *a, **kw: None,
    "install_asyncio_exception_handler": lambda *a, **kw: None,
    "configure_service_observability": lambda *a, **kw: None,
    "get_log_context": lambda: {},
    "enrich_payload_with_context": lambda p: p or {},
    "observe_db_query": lambda *a, **kw: None,
    "bind_context_from_payload": lambda *a, **kw: None,
    "observe_work_duration": lambda *a, **kw: MagicMock(),
    "record_agent_shutdown": lambda *a, **kw: None,
    "record_agent_started": lambda *a, **kw: None,
    "record_event_emitted": lambda *a, **kw: None,
    "record_task_claimed": lambda *a, **kw: None,
    "record_task_completed": lambda *a, **kw: None,
    "record_task_failed": lambda *a, **kw: None,
    "set_active_work": lambda *a, **kw: None,
})

# -- shared.agent_state --
_fake_as = _ensure_fake("shared.agent_state", {
    "validate_transition": lambda *a, **kw: True,
})


class _AgentState:
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class _PauseReason:
    MANUAL = "manual"
    BUDGET = "budget"
    SYSTEM = "system"
    ERROR_THRESHOLD = "error_threshold"
    OPERATOR = "operator"


_fake_as.AgentState = _AgentState
_fake_as.PauseReason = _PauseReason

# -- shared.daemon_memory --
_ensure_fake("shared.daemon_memory", {
    "DaemonMemoryStore": MagicMock(),
    "MemoryCache": MagicMock(),
    "WorkingMemory": MagicMock(),
})

# -- shared.db (the big one) --
_fake_db = _ensure_fake("shared.db", {
    "init_pool": AsyncMock(),
    "close_pool": AsyncMock(),
    "execute": AsyncMock(),
    "fetch_all": AsyncMock(return_value=[]),
    "fetch_one": AsyncMock(return_value=None),
    "fetch_val": AsyncMock(return_value=0),
    "emit_event": AsyncMock(),
    "insert_task": AsyncMock(return_value=None),
    "get_config": AsyncMock(return_value=None),
    "set_config": AsyncMock(),
})

# -- aiohttp (orchestrator imports it) --
_ensure_fake("aiohttp", {
    "ClientSession": MagicMock(),
    "ClientTimeout": MagicMock(),
})

# -- uvicorn --
_ensure_fake("uvicorn", {"Config": MagicMock(), "Server": MagicMock()})

# -- shared.pipeline --
_ensure_fake("shared.pipeline", {
    "assess_pipeline_state": AsyncMock(return_value={}),
})

# -- perseus.agent_registry --
_ensure_fake("perseus.agent_registry", {
    "check_agent_health": AsyncMock(return_value=[]),
    "heartbeat": AsyncMock(),
})

# -- perseus.scheduler --
_ensure_fake("perseus.scheduler", {
    "SCHEDULES": [],
    "SCHEDULE_MAP": {},
})

# -- shared.comms (orchestrator followup loop) --
_ensure_fake("shared.comms", {
    "ask_agent": AsyncMock(return_value={"answer": "ok"}),
    "send_alert": AsyncMock(),
    "is_agent_alive": AsyncMock(return_value=True),
    "delegate_task": AsyncMock(),
    "store_learning": AsyncMock(),
    "request_task": AsyncMock(return_value=None),
})

# -- shared.llm_client --
_fake_llm = MagicMock()
_fake_llm.generate = AsyncMock(return_value='{"tasks":[],"reasoning":"test"}')
_ensure_fake("shared.llm_client", {"llm": _fake_llm})

# -- shared.a2a_wrapper --
_ensure_fake("shared.a2a_wrapper", {
    "AgentCard": MagicMock(),
    "create_a2a_app": MagicMock(),
})

# -- shared.oj_bridge --
_ensure_fake("shared.oj_bridge", {
    "get_bus": MagicMock(return_value=MagicMock()),
    "get_trace_store": MagicMock(),
    "get_agent_manager": MagicMock(),
    "get_audit_logger": MagicMock(),
    "call_agent_async": AsyncMock(return_value={}),
})

# -- shared.integration_boot --
_ensure_fake("shared.integration_boot", {
    "boot_integration": AsyncMock(return_value="ok"),
})

# -- openjarvis submodules --
# The real openjarvis package uses Python 3.10+ features (slots=True) that
# are not available in the test runtime (Python 3.9). Fake the submodules
# that cause import failures in the transitive dependency chain.
_ensure_fake("openjarvis.security.credential_stripper", {
    "CredentialStripper": MagicMock(),
})
_ensure_fake("openjarvis.vassals.supervisor", {"VassalSupervisor": MagicMock()})
_ensure_fake("openjarvis.vassals.discovery", {"VassalDiscovery": MagicMock()})
_ensure_fake("openjarvis.vassals.event_relay", {"EventRelay": MagicMock()})
_ensure_fake("openjarvis.vassals.perseus_scheduler", {
    "PerseusConfig": MagicMock(),
    "PerseusScheduler": MagicMock(),
})
_ensure_fake("openjarvis.vassals.sleep_cycle", {"run_sleep_cycle": AsyncMock()})
_ensure_fake("openjarvis.vassals.cell_division", {"evaluate_division_need": AsyncMock()})
_ensure_fake("openjarvis.core.events", {"EventType": MagicMock()})
_ensure_fake("openjarvis.operators.manager", {"OperatorManager": MagicMock()})
_ensure_fake("openjarvis.system", {"SystemBuilder": MagicMock()})
_ensure_fake("openjarvis.a2a.client", {"A2AClient": MagicMock()})

# -- shared.wakeup_queue --
_ensure_fake("shared.wakeup_queue", {
    "WakeupQueue": MagicMock(),
    "_wakeup_mode": lambda: "off",
})

# -- tools --
_ensure_fake("tools")
_ensure_fake("tools.budget_guard", {
    "BudgetGuard": MagicMock(),
    "get_budget_report": AsyncMock(return_value={}),
})

# -- perseus.health --
_ensure_fake("perseus.health", {
    "check_infrastructure": AsyncMock(return_value={}),
})

# -- perseus.self_audit --
_ensure_fake("perseus.self_audit", {
    "run_self_audit": AsyncMock(return_value={"total_findings": 0, "approved": 0, "applied": 0}),
    "_analyze_file": AsyncMock(return_value=[]),
})

# -- perseus.scout --
_ensure_fake("perseus.scout", {
    "run_scout_cycle": AsyncMock(return_value={"total": 0, "new": 0, "actionable": 0}),
})

# -- shared.agent_loader --
_ensure_fake("shared.agent_loader", {
    "load_agent_specs": MagicMock(return_value=[]),
})

# Ensure feature flag is on
os.environ["ANATOMY_GENERATOR_LOOP"] = "true"

from shared.agent_loop import AbortSignal, TerminalReason, TickResult  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daemon():
    """Create a PerseusDaemon with mocked heavy methods."""
    # Force reimport to pick up the faked modules
    for mod_name in list(sys.modules):
        if mod_name.startswith("perseus.daemon"):
            del sys.modules[mod_name]

    from perseus.daemon import PerseusDaemon

    daemon = PerseusDaemon()
    # Stub out methods that touch DB/network
    daemon.register = AsyncMock()
    daemon.finalize_shutdown = AsyncMock()
    daemon._tick = AsyncMock()
    return daemon


# ---------------------------------------------------------------------------
# Task 26b-05: PerseusDaemon generator tests
# ---------------------------------------------------------------------------

class TestPerseusDaemonGenerator:
    """Tests for PerseusDaemon.tick_generator()."""

    @pytest.mark.asyncio
    async def test_perseus_daemon_yields_ticks(self):
        """Generator produces TickResult objects on each tick."""
        daemon = _make_daemon()
        daemon._running = True

        ticks_collected: list[TickResult] = []
        tick_count = 0

        async for tick in daemon.tick_generator():
            ticks_collected.append(tick)
            tick_count += 1
            if tick_count >= 3:
                daemon._running = False

        assert len(ticks_collected) == 3
        for i, tick in enumerate(ticks_collected):
            # Use class name check instead of isinstance to handle module
            # identity mismatches when conftest restores modules.
            assert type(tick).__name__ == "TickResult"
            assert tick.turn_number == i
            assert tick.content == "tick_ok"
            assert tick.terminal is None

    @pytest.mark.asyncio
    async def test_perseus_daemon_respects_abort(self):
        """Abort signal stops the generator with OPERATOR_ABORT terminal."""
        daemon = _make_daemon()
        daemon._running = True

        abort = AbortSignal()
        abort.abort()

        ticks = []
        async for tick in daemon.tick_generator(abort_signal=abort):
            ticks.append(tick)

        assert len(ticks) == 1
        assert ticks[0].is_terminal
        assert ticks[0].terminal == TerminalReason.OPERATOR_ABORT
        assert "abort" in ticks[0].terminal_message.lower()

    @pytest.mark.asyncio
    async def test_perseus_daemon_yields_error_ticks(self):
        """When _tick raises, the generator yields a tick with error content."""
        daemon = _make_daemon()
        daemon._running = True

        call_count = 0

        async def _failing_tick():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated tick failure")

        daemon._tick = _failing_tick

        ticks = []
        count = 0
        async for tick in daemon.tick_generator():
            ticks.append(tick)
            count += 1
            if count >= 3:
                daemon._running = False

        assert len(ticks) == 3
        assert ticks[0].content == "tick_ok"
        assert "tick_error" in ticks[1].content
        assert "simulated tick failure" in ticks[1].content
        assert ticks[2].content == "tick_ok"

    @pytest.mark.asyncio
    async def test_perseus_daemon_abort_mid_loop(self):
        """Abort signal set after first tick stops the generator."""
        daemon = _make_daemon()
        daemon._running = True

        abort = AbortSignal()

        ticks = []
        async for tick in daemon.tick_generator(abort_signal=abort):
            ticks.append(tick)
            abort.abort()

        assert len(ticks) == 2
        assert not ticks[0].is_terminal
        assert ticks[1].is_terminal
        assert ticks[1].terminal == TerminalReason.OPERATOR_ABORT

    @pytest.mark.asyncio
    async def test_perseus_daemon_running_false_stops(self):
        """Setting _running=False stops the generator naturally."""
        daemon = _make_daemon()
        daemon._running = True

        ticks = []
        async for tick in daemon.tick_generator():
            ticks.append(tick)
            daemon._running = False

        assert len(ticks) == 1
        assert not ticks[0].is_terminal


# ---------------------------------------------------------------------------
# Task 26b-06: Orchestrator _run_loop tests
# ---------------------------------------------------------------------------

class TestOrchestratorRunLoop:
    """Tests for the _run_loop utility."""

    @pytest.mark.asyncio
    async def test_orchestrator_run_loop_consumes(self):
        """_run_loop consumes generator ticks until terminal."""
        from orchestrator import _run_loop

        async def _gen():
            for i in range(3):
                yield TickResult(content=f"tick-{i}", turn_number=i)
            yield TickResult(
                content="done",
                terminal=TerminalReason.COMPLETED,
                terminal_message="all done",
                turn_number=3,
            )

        await _run_loop("test_loop", _gen())

    @pytest.mark.asyncio
    async def test_orchestrator_run_loop_logs(self):
        """_run_loop logs tick results — verify via mock handler since custom logger bypasses pytest capture."""
        from orchestrator import _run_loop

        log_messages = []
        handler = logging.Handler()
        handler.emit = lambda record: log_messages.append(record.getMessage())

        orch_logger = logging.getLogger("perseus.orchestrator")
        orch_logger.addHandler(handler)
        old_level = orch_logger.level
        orch_logger.setLevel(logging.DEBUG)

        try:
            async def _gen():
                yield TickResult(content="normal tick", turn_number=0)
                yield TickResult(
                    content="final",
                    terminal=TerminalReason.COMPLETED,
                    terminal_message="finished",
                    turn_number=1,
                )

            await _run_loop("log_test", _gen())

            log_text = " ".join(log_messages)
            assert "log_test" in log_text
            assert "terminated" in log_text.lower() or "COMPLETED" in log_text
        finally:
            orch_logger.removeHandler(handler)
            orch_logger.setLevel(old_level)

    @pytest.mark.asyncio
    async def test_orchestrator_run_loop_abort_signal(self):
        """_run_loop respects abort signal."""
        from orchestrator import _run_loop

        abort = AbortSignal()
        tick_count = 0

        async def _gen():
            nonlocal tick_count
            while True:
                yield TickResult(content="tick", turn_number=tick_count)
                tick_count += 1

        abort.abort()
        await _run_loop("abort_test", _gen(), abort_signal=abort)

        assert tick_count <= 1

    @pytest.mark.asyncio
    async def test_orchestrator_run_loop_empty_generator(self):
        """_run_loop handles a generator that yields nothing."""
        from orchestrator import _run_loop

        async def _gen():
            return
            yield  # noqa: E501 — make it a generator

        await _run_loop("empty_test", _gen())


# ---------------------------------------------------------------------------
# Backward compatibility: start() still works
# ---------------------------------------------------------------------------

class TestBackwardCompatStart:
    """Verify existing start() callers work unchanged."""

    @pytest.mark.asyncio
    async def test_backward_compat_start(self):
        """start() works when ANATOMY_GENERATOR_LOOP=true (generator path)."""
        daemon = _make_daemon()
        tick_count = 0

        async def _counted_tick():
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                daemon._running = False

        daemon._tick = _counted_tick

        # Ensure shared.db functions are AsyncMocks so daemon.start()
        # can call db.init_pool() without a real database.
        import shared.db as _db
        _orig_init = getattr(_db, "init_pool", None)
        _orig_close = getattr(_db, "close_pool", None)
        _db.init_pool = AsyncMock()
        _db.close_pool = AsyncMock()
        try:
            await daemon.start()
        finally:
            if _orig_init is not None:
                _db.init_pool = _orig_init
            if _orig_close is not None:
                _db.close_pool = _orig_close

        assert tick_count >= 2
