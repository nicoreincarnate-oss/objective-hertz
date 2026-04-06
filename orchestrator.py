"""OpenJarvis Orchestrator — THE system brain.

OpenJarvis is the boss. All strategic decisions, vassal lifecycle,
observability, and self-optimization flow through here.

Architecture:
- VassalSupervisor  → spawns & monitors Titan/Hermes/ClawdBot
- VassalDiscovery   → discovers A2A capabilities dynamically
- PerseusScheduler  → strategic brain (assess, prioritize, budget, health, dispatch)
- EventRelay        → bidirectional event bridge between boss and vassals

NO direct imports from Titan/Hermes/ClawdBot pipeline code.
All vassal interaction goes through A2A.

Usage:
    python orchestrator.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import signal
import sys
import threading
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from shared.agent_loop import AbortSignal, TickResult
from shared.config import config
from shared.logging_config import setup_logging
from shared.observability import capture_exception, install_asyncio_exception_handler

logger = setup_logging("orchestrator")


def _generator_loop_enabled() -> bool:
    """Check ANATOMY_GENERATOR_LOOP feature flag."""
    return os.environ.get("ANATOMY_GENERATOR_LOOP", "").lower() in ("true", "1")


async def _run_loop(
    name: str,
    generator: AsyncGenerator[TickResult, None],
    *,
    abort_signal: AbortSignal | None = None,
) -> None:
    """Consume an async generator loop, logging each tick result.

    This utility replaces the repeated ``while self._running`` pattern
    across orchestrator loops.  It:
    - Consumes the generator until a terminal TickResult
    - Logs each tick at DEBUG level, terminals at INFO
    - Checks an optional abort signal between ticks
    - Stops cleanly on terminal results

    Phase 26b-06 (Anatomy Integration A-06).
    Gated behind ANATOMY_GENERATOR_LOOP feature flag.
    """
    async for tick in generator:
        if abort_signal and abort_signal.is_aborted:
            logger.info("[%s] abort signal received, stopping loop", name)
            break

        if tick.is_terminal:
            logger.info(
                "[%s] loop terminated: %s — %s",
                name,
                tick.terminal,
                tick.terminal_message,
            )
            break

        logger.debug(
            "[%s] tick %d: %s",
            name,
            tick.turn_number,
            tick.content[:120] if tick.content else "ok",
        )


# ---------------------------------------------------------------------------
# Phase 18b-06: Fast-path dispatch for --status / --health / --version
#
# These flags query the *running* orchestrator (or print static info) and
# exit immediately — no full boot, no DB pool, no vassal spawning needed.
# ---------------------------------------------------------------------------

_ORCHESTRATOR_VERSION = "0.9.0"


def _fast_path_dispatch() -> bool:
    """Handle lightweight CLI queries without booting the full system.

    Returns True if a fast-path flag was handled (caller should sys.exit),
    False otherwise (proceed with normal boot).
    """
    if len(sys.argv) < 2:
        return False

    flag = sys.argv[1]

    if flag == "--version":
        print(f"OpenJarvis Orchestrator v{_ORCHESTRATOR_VERSION}")
        return True

    if flag in ("--status", "--health"):
        import urllib.error
        import urllib.request

        a2a_port = int(os.environ.get("ORCHESTRATOR_A2A_PORT", "9000"))
        url = f"http://localhost:{a2a_port}/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read().decode()
                if flag == "--health":
                    # --health: print full JSON response
                    print(body)
                else:
                    # --status: concise one-liner
                    try:
                        data = json.loads(body)
                        status = data.get("status", "unknown")
                        print(f"OpenJarvis: {status} (port {a2a_port})")
                    except (json.JSONDecodeError, KeyError):
                        print(f"OpenJarvis: running (port {a2a_port})")
        except urllib.error.URLError:
            print(f"OpenJarvis: not reachable on port {a2a_port}")
            sys.exit(1)
        except (OSError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            print(f"OpenJarvis: error querying health — {exc}")
            sys.exit(1)
        return True

    return False

# Vassal A2A endpoints
VASSAL_CONFIG = {
    "titan": {"url": f"http://localhost:{os.environ.get('TITAN_A2A_PORT', '9001')}"},
    "hermes": {"url": f"http://localhost:{os.environ.get('HERMES_A2A_PORT', '9002')}"},
    "clawdbot": {"url": f"http://localhost:{os.environ.get('CLAWDBOT_A2A_PORT', '9003')}"},
    "deerflow_research": {"url": f"http://localhost:{os.environ.get('DEERFLOW_RESEARCH_A2A_PORT', '9011')}"},
}

PRODUCTION_TOOL_NAMES = [
    "think",
    "web_search",
    "file_read",
    "shell_exec",
    "memory_store",
    "memory_retrieve",
    "memory_search",
    "channel_send",
    "channel_list",
    "channel_status",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_extract",
    "browser_screenshot",
]

FRAMEWORK_AGENT_SPECS = [
    {
        "name": "openjarvis-boss-reasoner",
        "agent_type": "orchestrator",
        "config": {
            "model": "smart",  # Sonnet — strategic reasoning requires depth
            "instruction": (
                "Act as OpenJarvis's strategic reasoning core. Investigate system issues, "
                "inspect runtime state, and produce actionable recommendations for the boss.\n"
                "\n"
                "KEY FILES TO REASON ABOUT:\n"
                "- orchestrator.py — top-level entry point, daemon lifecycle, agent registration\n"
                "- shared/db.py — Postgres pool, system_config table (get_config/set_config)\n"
                "- titan/daemon.py — revenue pipeline state, stage progression, stuck tasks\n"
                "- perseus/scheduler.py — 15 scheduled task definitions, cron health\n"
                "\n"
                "METRICS TO CHECK:\n"
                "- budget_tracking totals: SELECT SUM(cost) FROM budget_tracking WHERE date = CURRENT_DATE\n"
                "- task_queue depth: SELECT status, COUNT(*) FROM task_queue GROUP BY status\n"
                "- agent_decisions recent: SELECT * FROM agent_decisions ORDER BY created_at DESC LIMIT 20\n"
                "- system_config flags: SELECT key, value FROM system_config WHERE key LIKE 'ANATOMY_%%'\n"
                "\n"
                "OUTPUT FORMAT:\n"
                "Return a JSON object with keys: finding (str), severity (P0-P3), "
                "affected_component (str), recommended_action (str), evidence (list[str])."
            ),
            "tools": [
                "think", "file_read", "shell_exec", "memory_store",
                "memory_retrieve", "memory_search", "channel_status",
            ],
            "schedule_type": "interval",
            "schedule_value": 600,
            "router_policy": "heuristic",
            "learning_enabled": True,
            "learning_schedule": "every_20_ticks",
            "max_turns": 12,
            "temperature": 0.2,
        },
    },
    {
        "name": "openjarvis-react-investigator",
        "agent_type": "native_react",
        "config": {
            "model": "fast",  # Haiku — log inspection is classification
            "instruction": (
                "Use ReAct to inspect logs, search for failures, and gather concrete evidence "
                "about runtime regressions before escalation.\n"
                "\n"
                "LOG PATHS TO INSPECT:\n"
                "- logs/perseus.log — scheduler daemon, task execution, cron drift\n"
                "- logs/titan.log — revenue pipeline stages, lead processing failures\n"
                "- logs/hermes.log — alert dispatch, Telegram delivery, API errors\n"
                "- logs/clawdbot.log — browser automation, site builds, Netlify deploys\n"
                "\n"
                "ERROR PATTERNS TO SEARCH FOR:\n"
                "- 'Traceback' or 'Exception' — unhandled errors\n"
                "- 'timeout' or 'ConnectionRefused' — infrastructure failures\n"
                "- 'budget_exceeded' or 'rate_limit' — cost overruns\n"
                "- 'FATAL' or 'CRITICAL' — daemon-killing errors\n"
                "\n"
                "DB TABLES FOR DIAGNOSIS:\n"
                "- events: SELECT * FROM events WHERE level = 'error' ORDER BY created_at DESC LIMIT 50\n"
                "- task_queue: SELECT * FROM task_queue WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 20\n"
                "- budget_tracking: SELECT * FROM budget_tracking WHERE date = CURRENT_DATE\n"
                "\n"
                "INVESTIGATION METHODOLOGY:\n"
                "1. Read the most recent 200 lines of each log file\n"
                "2. Search for error patterns across all logs\n"
                "3. Cross-reference errors with task_queue and events tables\n"
                "4. Produce a structured finding: {file, line_range, error_class, root_cause, fix_suggestion}"
            ),
            "tools": [
                "think", "file_read", "shell_exec", "memory_store",
                "memory_retrieve", "memory_search",
            ],
            # Phase 21 — read-only restriction: investigators observe, never mutate
            "disallowed_tools": [
                "browser_click", "browser_type", "browser_navigate",
                "file_write", "file_delete",
            ],
            "permission_level": "supervised",
            "schedule_type": "interval",
            "schedule_value": 900,
            "router_policy": "heuristic",
            "learning_enabled": True,
            "learning_schedule": "every_20_ticks",
            "max_turns": 10,
            "temperature": 0.2,
        },
    },
    {
        "name": "openjarvis-runtime-monitor",
        "agent_type": "monitor_operative",
        "config": {
            "model": "fast",  # Haiku — monitoring is classification
            "context_level": "minimal",  # Strip heavy context for cost savings
            "instruction": (
                "Monitor OpenJarvis runtime health, tool failures, scheduler drift, and agent "
                "activity. Persist state between ticks and escalate actionable anomalies.\n"
                "\n"
                "HEALTH CHECK ENDPOINTS TO PROBE:\n"
                "- http://localhost:9000/health — orchestrator A2A server\n"
                "- http://localhost:9001/health — Titan revenue daemon\n"
                "- http://localhost:9002/health — Hermes alerts daemon\n"
                "- http://localhost:9003/health — ClawdBot site builder\n"
                "\n"
                "DAEMON PROCESSES TO VERIFY (via shell_exec 'pgrep -f'):\n"
                "- perseus/daemon.py — scheduler must be running\n"
                "- titan/daemon.py — revenue pipeline must be running\n"
                "- hermes/daemon.py — alert dispatch must be running\n"
                "- clawdbot/daemon.py — site builder must be running\n"
                "\n"
                "DB CONNECTIVITY CHECKS:\n"
                "- Run: SELECT 1 FROM system_config LIMIT 1 — validates Postgres is reachable\n"
                "- Check pool: SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()\n"
                "\n"
                "ALERT THRESHOLDS:\n"
                "- Daemon health endpoint non-200 for >2 consecutive checks → P1 escalation\n"
                "- task_queue WHERE status = 'failed' count > 10 in last hour → P1 escalation\n"
                "- budget_tracking daily cost > 80%% of $800/30 daily budget → P2 warning\n"
                "- No agent_decisions rows in last 30 minutes → P2 stall warning"
            ),
            "tools": [
                "think", "memory_store", "memory_retrieve", "memory_search",
                "shell_exec", "file_read", "channel_status",
            ],
            "schedule_type": "interval",
            "schedule_value": 300,
            "router_policy": "heuristic",
            "learning_enabled": True,
            "learning_schedule": "every_20_ticks",
            "timeout_seconds": 120,
            "max_stall_retries": 3,
            "max_turns": 15,
            "temperature": 0.2,
            "memory_extraction": "structured_json",
            "observation_compression": "summarize",
            "retrieval_strategy": "hybrid_with_self_eval",
            "task_decomposition": "phased",
        },
    },
]


class Orchestrator:
    """OpenJarvis — the sole orchestrator and boss of all vassals."""

    def __init__(self):
        self._running = False
        self._supervisor = None
        self._discovery = None
        self._scheduler = None
        self._relay = None
        self._oj_system = None
        self._operator_manager = None
        self._learning_handler = None
        self._loop_tasks: list[asyncio.Task] = []
        # Phase 26a-05 (A-15): Deferred post-first-tick initialization
        self._first_tick_done = asyncio.Event()
        self._post_first_tick_launched = False

    # ── A2A Handler ───────────────────────────────────────────────────

    async def handle_a2a(self, input_text: str) -> str:
        """Handle incoming A2A requests from other agents."""
        try:
            req = json.loads(input_text)
        except (ValueError, TypeError):
            req = {"capability": "ask", "params": {"question": input_text}}

        capability = req.get("capability", "ask")
        params = req.get("params", {})

        if capability == "health_check":
            return json.dumps({
                "status": "running" if self._running else "stopped",
                "agent": "openjarvis",
            })

        if capability == "operator_command":
            text = params.get("text", params.get("description", ""))
            if text:
                await self._handle_command(text)
                return json.dumps({"status": "accepted", "command": text[:100]})
            return json.dumps({"error": "text is required"})

        if capability == "pipeline_state":
            from shared.pipeline import assess_pipeline_state
            state = await assess_pipeline_state()
            return json.dumps(state, default=str)

        if capability == "vassal_status":
            if self._supervisor:
                return json.dumps(self._supervisor.status(), default=str)
            return json.dumps({"error": "supervisor not initialized"})

        if capability == "vassal_restart":
            name = params.get("name", "")
            if name and self._supervisor:
                ok = await self._supervisor.restart(name)
                return json.dumps({"restarted": ok, "vassal": name})
            return json.dumps({"error": "name required or supervisor not ready"})

        if capability == "scheduler_status":
            if self._scheduler:
                return json.dumps(self._scheduler.status(), default=str)
            return json.dumps({"error": "scheduler not initialized"})

        if capability == "budget_report":
            try:
                from tools.budget_guard import get_budget_report
                report = await get_budget_report()
                return json.dumps(report, default=str)
            except (ImportError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                return json.dumps({"error": str(exc)})

        if capability == "sleep_cycle_trigger":
            asyncio.create_task(self._run_sleep_cycle())
            return json.dumps({"status": "sleep cycle triggered"})

        if capability == "ask":
            question = params.get("question", input_text)
            return json.dumps({
                "answer": f"OpenJarvis received: {question[:200]}",
                "agent": "openjarvis",
            })

        return json.dumps({"error": f"Unknown capability: {capability}"})

    # ── Startup ───────────────────────────────────────────────────────

    async def start(self):
        """Start OpenJarvis — the boss boots up."""
        self._running = True
        logger.info("=" * 60)
        logger.info("  OPENJARVIS — THE BOSS IS STARTING")
        logger.info("=" * 60)

        # 1. Boot OJ runtime (sync — no DB needed)
        from shared.oj_bridge import (
            get_agent_manager,
            get_audit_logger,
            get_bus,
            get_trace_store,
        )
        bus = get_bus()
        get_trace_store()
        get_agent_manager()
        get_audit_logger()

        # 2. DB pool init — MUST complete before integration boot and framework
        # bootstrap because both write to / read from Postgres.
        from shared.db import init_pool
        await init_pool()
        logger.info("OJ runtime + DB pool initialized")

        # Phase 18b-05: Parallelize independent boot phases.
        #
        # Dependency analysis:
        #   - boot_integration() needs DB pool (telemetry writes metrics) but
        #     does NOT need the OJ framework (tools, agents, operators).
        #   - _bootstrap_openjarvis_framework() needs DB pool (SystemBuilder
        #     persists agent configs) and the event bus, but does NOT need
        #     integration modules (tool registry, MCP, channels).
        #   - Therefore these two can safely run concurrently after init_pool().
        from shared.integration_boot import boot_integration
        integration_status, _ = await asyncio.gather(
            boot_integration("orchestrator"),
            self._bootstrap_openjarvis_framework(bus),
        )
        logger.info("Integration boot status: %s", integration_status)

        # 2. Start vassals (with port-probe reconciliation)
        project_dir = os.path.dirname(os.path.abspath(__file__))
        from openjarvis.vassals.supervisor import VassalSupervisor
        self._supervisor = VassalSupervisor(bus, project_dir)
        self._supervisor.register_defaults(project_dir)
        await self._probe_and_start_vassals()
        logger.info("Vassal supervisor ready")

        # Wait for A2A servers to come up (readiness probe replaces fixed sleep)
        await self._wait_for_vassals_ready()

        # 3. Discover capabilities
        from openjarvis.vassals.discovery import VassalDiscovery
        self._discovery = VassalDiscovery(bus, config=VASSAL_CONFIG)
        self._discovery.discover_all()
        total_caps = sum(len(v.capabilities) for v in self._discovery.vassals.values())
        logger.info("Discovered %d capabilities across %d vassals",
                     total_caps, len(self._discovery.vassals))

        # 4. Wire EventRelay
        from openjarvis.vassals.event_relay import EventRelay
        self._relay = EventRelay(bus, self._discovery)
        logger.info("EventRelay wired")

        # 5. Start PerseusScheduler — THE strategic brain
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        sched_config = PerseusConfig(
            tick_interval=int(os.environ.get("SCHEDULER_TICK_INTERVAL", "60")),
            budget_monthly_cap=float(os.environ.get("MONTHLY_BUDGET_CAP", "800")),
        )

        budget_guard = None
        try:
            from tools.budget_guard import BudgetGuard
            budget_guard = BudgetGuard()
        except (ImportError, OSError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            pass

        # Create WakeupQueue (Phase 16: event-driven wakeup)
        wakeup_queue = None
        try:
            from shared.wakeup_queue import WakeupQueue, _wakeup_mode
            if _wakeup_mode() != "off":
                wakeup_queue = WakeupQueue()
                logger.info("WakeupQueue created (mode=%s)", _wakeup_mode())
            else:
                logger.info("WakeupQueue disabled (EVENT_WAKEUP_ENABLED not set)")
        except ImportError:
            logger.debug("WakeupQueue not available (shared/wakeup_queue.py missing)")

        self._scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=self._discovery,
            config=sched_config,
            budget_guard=budget_guard,
            wakeup_queue=wakeup_queue,
        )
        logger.info("PerseusScheduler configured (tick=%ds)", sched_config.tick_interval)

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, self._request_shutdown)

        # 6. Run everything concurrently
        logger.info("=" * 60)
        logger.info("  OPENJARVIS IS LIVE — ALL SYSTEMS GO")
        logger.info("=" * 60)

        try:
            # Phase 26a-05 (A-15): When ANATOMY_RUNTIME_STATE is active, defer
            # non-critical loops (scout, deerflow, self-audit) until after the
            # first scheduler tick completes.  This reduces boot-time contention
            # and lets the strategic brain assess state before background work.
            if self._deferred_startup_enabled():
                logger.info("Deferred startup active — scout/deerflow/audit deferred to post-first-tick")
                loops = [
                    self._scheduler_with_first_tick_signal(),  # Signals after tick 1
                    self._relay.start_polling(),   # Event bridge (lightweight)
                    self._command_loop(),          # Operator commands (critical)
                    self._followup_loop(),         # Stale task monitoring (critical)
                    self._post_first_tick_init(),  # Launches deferred loops after tick 1
                ]
            else:
                loops = [
                    self._scheduler.start(),       # Strategic brain
                    self._relay.start_polling(),   # Event bridge
                    self._command_loop(),          # Operator commands
                    self._followup_loop(),         # Stale task monitoring
                    self._sleep_cycle_loop(),      # Nightly optimization
                    self._scout_loop(),            # External intelligence
                    self._deerflow_loop(),         # Continuous evolution research
                    self._self_audit_loop(),       # Codebase self-audit
                ]
            # Memory bus subscriber — ingest memory.changed events into MAGMA
            loops.append(self._memory_bus_loop())

            if config.ruflo.enabled:
                loops.append(self._ruflo_validation_loop())  # Ruflo fix validation
                loops.append(self._ruflo_maintenance_loop())  # Weekly maintenance
                logger.info("Ruflo validation + maintenance loops enabled")
            await asyncio.gather(*loops)
        finally:
            await self._cleanup()

    # ── Vassal Management ─────────────────────────────────────────────

    async def _probe_and_start_vassals(self):
        """Probe A2A ports and adopt already-running vassals, spawn the rest."""
        from openjarvis.a2a.client import A2AClient

        for name, vassal in self._supervisor._vassals.items():
            # Try to reach an existing process on the port
            try:
                client = A2AClient(
                    f"http://localhost:{vassal.a2a_port}", timeout=2.0,
                )
                client.discover()
                vassal.status = "running"
                logger.info("Adopted already-running vassal %s on port %d",
                            name, vassal.a2a_port)
            except (OSError, ConnectionError, TimeoutError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                # Not running — spawn it
                ok = await self._supervisor.start(name)
                if ok:
                    logger.info("Spawned vassal %s (pid=%s)", name, vassal.pid)
                else:
                    logger.error("Failed to spawn vassal %s", name)

        # Start crash monitor
        self._supervisor._monitor_task = asyncio.create_task(
            self._supervisor._monitor_loop()
        )

    async def _wait_for_vassals_ready(
        self,
        timeout: float = 15.0,
        interval: float = 0.5,
    ) -> None:
        """Poll vassal health endpoints until all respond 200 or timeout.

        Replaces a fixed ``asyncio.sleep(3)`` with an active readiness probe
        so the orchestrator proceeds as soon as vassals are truly healthy.
        """
        import aiohttp

        # Guard: supervisor not yet created or no vassals registered
        if self._supervisor is None:
            logger.info("Readiness probe skipped: supervisor not initialized")
            return

        vassals = getattr(self._supervisor, "_vassals", {})
        if not vassals:
            logger.info("Readiness probe skipped: no vassals registered")
            return

        # Build URL map for all registered vassals
        health_targets: dict[str, str] = {}
        for name, vassal in vassals.items():
            port = getattr(vassal, "a2a_port", None)
            if port:
                health_targets[name] = f"http://localhost:{port}/a2a/health"

        if not health_targets:
            logger.info("Readiness probe skipped: no vassal health endpoints")
            return

        logger.info(
            "Readiness probe: waiting for %d vassals (%s)",
            len(health_targets),
            ", ".join(health_targets),
        )

        elapsed = 0.0
        ready: set[str] = set()
        while elapsed < timeout:
            ready = set()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as session:
                    for name, url in health_targets.items():
                        try:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    ready.add(name)
                        except (OSError, ConnectionError, TimeoutError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                            pass  # vassal not ready yet
            except (OSError, ConnectionError, TimeoutError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # session-level error, retry

            if ready == set(health_targets):
                logger.info(
                    "All vassals ready after %.1fs", elapsed,
                )
                return

            await asyncio.sleep(interval)
            elapsed += interval

        not_ready = set(health_targets) - ready
        logger.warning(
            "Readiness probe timed out after %.1fs — not ready: %s (proceeding anyway)",
            timeout,
            ", ".join(sorted(not_ready)),
        )

    # ── Phase 26a-05 (A-15): Deferred post-first-tick startup ─────────

    @staticmethod
    def _deferred_startup_enabled() -> bool:
        """Check if deferred startup is gated on via ANATOMY_RUNTIME_STATE."""
        return os.environ.get("ANATOMY_RUNTIME_STATE", "").lower() in ("1", "true", "yes")

    async def _scheduler_with_first_tick_signal(self):
        """Wrap the scheduler loop: signal _first_tick_done after tick 1."""
        # The scheduler's _tick_count starts at 0 and increments at the
        # beginning of each _tick().  We monitor it from a parallel task
        # while the scheduler runs normally.
        monitor = asyncio.create_task(self._monitor_first_tick())
        try:
            await self._scheduler.start()
        finally:
            monitor.cancel()

    async def _monitor_first_tick(self):
        """Poll scheduler tick count and signal when first tick completes."""
        try:
            while not self._first_tick_done.is_set():
                if self._scheduler and getattr(self._scheduler, "_tick_count", 0) >= 1:
                    self._first_tick_done.set()
                    logger.info("First scheduler tick completed — launching deferred loops")
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def _post_first_tick_init(self):
        """Launch non-critical loops after the first scheduler tick.

        Defers: sleep_cycle, scout, deerflow, self-audit.
        These are background intelligence loops that do not need to run
        during boot — waiting for the first strategic tick reduces
        startup contention and lets the scheduler assess state first.
        """
        await self._first_tick_done.wait()
        if self._post_first_tick_launched:
            return
        self._post_first_tick_launched = True

        logger.info("Post-first-tick init: starting deferred loops")
        deferred = [
            self._sleep_cycle_loop(),
            self._scout_loop(),
            self._deerflow_loop(),
            self._self_audit_loop(),
        ]
        await asyncio.gather(*deferred)

    async def _bootstrap_openjarvis_framework(self, bus):
        """Boot the full OpenJarvis system layer inside production."""
        try:
            from openjarvis.core.events import EventType
            from openjarvis.operators.manager import OperatorManager
            from openjarvis.system import SystemBuilder

            builder = (
                SystemBuilder()
                .event_bus(bus)
                .agent("orchestrator")
                .tools(PRODUCTION_TOOL_NAMES)
                .scheduler(True)
                .workflow(True)
                .sessions(True)
            )
            self._oj_system = builder.build()
            if self._oj_system.scheduler is not None:
                self._oj_system.scheduler._system = self._oj_system
                self._oj_system.scheduler.start()

            self._ensure_framework_agents()

            self._operator_manager = OperatorManager(self._oj_system)
            self._oj_system.operator_manager = self._operator_manager
            ops_dir = Path(__file__).resolve().parent / "openjarvis" / "operators" / "data"
            manifests = self._operator_manager.discover(ops_dir)
            active_ops = self._activate_builtin_operators()

            self._learning_handler = self._make_learning_handler()
            bus.subscribe(EventType.AGENT_LEARNING_STARTED, self._learning_handler)

            logger.info(
                "OpenJarvis framework active: tools=%d managed_agents=%d operators=%d active_operators=%d",
                len(getattr(self._oj_system, "tools", []) or []),
                len(self._oj_system.agent_manager.list_agents()) if self._oj_system.agent_manager else 0,
                len(manifests),
                len(active_ops),
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.warning("OpenJarvis framework bootstrap failed: %s", exc, exc_info=True)

    def _ensure_framework_agents(self):
        """Create and schedule baseline managed OpenJarvis agents.

        When ANATOMY_DECLARATIVE_AGENTS flag is enabled, loads agent specs
        from TOML files in the agents/ directory. Falls back to the
        hardcoded FRAMEWORK_AGENT_SPECS if TOML loading fails or the
        flag is off (Phase 28-02 — D-26, D-27).
        """
        if not self._oj_system or not self._oj_system.agent_manager:
            return

        manager = self._oj_system.agent_manager
        scheduler = self._oj_system.agent_scheduler
        existing = {agent["name"]: agent for agent in manager.list_agents()}

        # Phase 28-02: Declarative agent definitions from TOML
        specs = FRAMEWORK_AGENT_SPECS
        if os.environ.get("ANATOMY_DECLARATIVE_AGENTS", "").lower() in ("true", "1"):
            try:
                from shared.agent_loader import load_agent_specs

                toml_specs = load_agent_specs()
                if toml_specs:
                    specs = toml_specs
                    logger.info(
                        "Loaded %d agent specs from TOML (declarative mode)",
                        len(toml_specs),
                    )
                else:
                    logger.warning(
                        "TOML agent specs empty, falling back to hardcoded FRAMEWORK_AGENT_SPECS",
                    )
            except (ImportError, OSError, ValueError, KeyError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning(
                    "Failed to load TOML agent specs, falling back to hardcoded: %s",
                    exc,
                )

        for spec in specs:
            desired_config = dict(spec["config"])
            current = existing.get(spec["name"])
            if current is None:
                current = manager.create_agent(
                    name=spec["name"],
                    agent_type=spec["agent_type"],
                    config=desired_config,
                )
                logger.info(
                    "Created managed OpenJarvis agent %s (%s)",
                    spec["name"], spec["agent_type"],
                )
            else:
                merged_config = dict(desired_config)
                merged_config.update(current.get("config", {}))
                manager.update_agent(
                    current["id"],
                    agent_type=spec["agent_type"],
                    config=merged_config,
                )
                current = manager.get_agent(current["id"])

            if scheduler and current:
                cfg = current.get("config", {})
                if cfg.get("schedule_type") in ("cron", "interval"):
                    if current["id"] not in scheduler.registered_agents:
                        scheduler.register_agent(current["id"])

        if scheduler and not scheduler.is_running:
            scheduler.start()

    def _activate_builtin_operators(self):
        """Activate a safe default set of built-in operators."""
        if not self._operator_manager:
            return []

        raw = os.environ.get("OPENJARVIS_BUILTIN_OPERATORS", "system_monitor")
        operator_ids = [name.strip() for name in raw.split(",") if name.strip()]
        active = []
        for operator_id in operator_ids:
            manifest = self._operator_manager.get_manifest(operator_id)
            if manifest is None:
                continue
            try:
                self._operator_manager.activate(operator_id)
                active.append(operator_id)
            except (RuntimeError, ValueError, KeyError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("Failed to activate operator %s: %s", operator_id, exc)
        return active

    def _make_learning_handler(self):
        """Create a bus callback that runs learning for managed agents."""
        def _handle_learning(event):
            orchestrator = getattr(self._oj_system, "_learning_orchestrator", None) if self._oj_system else None
            agent_id = getattr(event, "data", {}).get("agent_id", "")
            if orchestrator is None or not agent_id:
                return

            def _run():
                try:
                    result = orchestrator.run(agent_id=agent_id)
                    logger.info("Learning run for %s: %s", agent_id, result.get("status", "ok"))
                except (RuntimeError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.warning("Learning run failed for %s: %s", agent_id, exc)

            threading.Thread(target=_run, daemon=True, name=f"oj-learning-{agent_id[:6]}").start()

        return _handle_learning

    # ── Operator Command Loop (boss-specific) ─────────────────────────

    async def _command_loop(self):
        """Listen for operator commands and decompose into agent work.

        When ANATOMY_GENERATOR_LOOP is enabled, delegates to the generator
        version consumed via ``_run_loop``.
        """
        if _generator_loop_enabled():
            await _run_loop("command_loop", self._command_loop_gen())
            return

        while self._running:
            try:
                from shared.db import execute as db_execute
                from shared.db import fetch_all

                commands = await fetch_all(
                    """SELECT id, payload FROM task_queue
                       WHERE task_type = 'operator_command'
                       AND status = 'pending'
                       ORDER BY created_at ASC LIMIT 3"""
                )
                for cmd in commands or []:
                    task_id = cmd["id"]
                    payload = cmd.get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    text = payload.get("text", payload.get("description", ""))
                    if not text:
                        await db_execute(
                            "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                            (task_id,))
                        continue

                    await db_execute(
                        "UPDATE task_queue SET status = 'running' WHERE id = %s",
                        (task_id,))

                    await self._handle_command(text)

                    await db_execute(
                        "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                        (task_id,))

            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("Command loop: %s", exc)

            await asyncio.sleep(10)

    async def _command_loop_gen(self) -> AsyncGenerator[TickResult, None]:
        """Generator version of _command_loop (Phase 26b-06)."""
        turn = 0
        while self._running:
            processed = 0
            error_msg = ""
            try:
                from shared.db import execute as db_execute
                from shared.db import fetch_all

                commands = await fetch_all(
                    """SELECT id, payload FROM task_queue
                       WHERE task_type = 'operator_command'
                       AND status = 'pending'
                       ORDER BY created_at ASC LIMIT 3"""
                )
                for cmd in commands or []:
                    task_id = cmd["id"]
                    payload = cmd.get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    text = payload.get("text", payload.get("description", ""))
                    if not text:
                        await db_execute(
                            "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                            (task_id,))
                        continue

                    await db_execute(
                        "UPDATE task_queue SET status = 'running' WHERE id = %s",
                        (task_id,))
                    await self._handle_command(text)
                    await db_execute(
                        "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                        (task_id,))
                    processed += 1

            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                error_msg = str(exc)
                logger.warning("Command loop: %s", exc)

            yield TickResult(
                content=f"processed={processed}" + (f" error={error_msg}" if error_msg else ""),
                turn_number=turn,
            )
            turn += 1
            await asyncio.sleep(10)

    async def _handle_command(self, command: str):
        """Decompose a high-level operator command into agent tasks via LLM."""
        from shared.comms import delegate_task
        from shared.db import execute as db_execute
        from shared.llm_client import llm

        logger.info("Boss received command: %s", command[:100])

        try:
            plan = await llm.generate(
                f"You are OpenJarvis, the boss of a 4-agent business team:\n"
                f"- Titan: revenue pipeline (lead discovery, email outreach, deals, invoicing, payments)\n"
                f"- ClawdBot: skills executor (browser automation, web scraping, site building, research, image gen)\n"
                f"- Hermes: operator comms (Telegram alerts, dashboard, briefings)\n"
                f"- Ruflo: engineering (code fixes, code review, security scans, dependency audits, refactoring)\n\n"
                f"The operator commands: \"{command}\"\n\n"
                f"Decompose this into specific, actionable tasks for your agents.\n"
                f"Available task_types for Titan: lead_discovery, lead_research, email_compose, email_send, "
                f"follow_up_check, close_interested, build_sites, process_invoices, sync_analytics\n"
                f"Available task_types for ClawdBot: skill_execute, web_scrape, browser_task, enrich_lead, "
                f"site_verify, image_generation, capability_resolve\n"
                f"Available task_types for Hermes: send_alert, morning_briefing\n"
                f"Available task_types for Ruflo: code_fix, code_review, code_refactor, "
                f"security_scan, dependency_audit, implement_tool, test_generate\n\n"
                f"Return ONLY valid JSON:\n"
                f"{{\"tasks\": [{{\"agent\": \"titan|clawdbot|hermes|ruflo\", \"task_type\": \"...\", "
                f"\"description\": \"...\", \"priority\": 1}}], "
                f"\"reasoning\": \"why this plan\"}}",
                model="genius", max_tokens=800, temperature=0.3,
            )

            match = re.search(r'\{[\s\S]*\}', plan)
            if not match:
                logger.error("Boss couldn't parse plan from LLM: %s", plan[:200])
                return

            parsed = json.loads(match.group())
            tasks = parsed.get("tasks", [])
            reasoning = parsed.get("reasoning", "")

            logger.info("Boss plan: %d tasks — %s", len(tasks), reasoning[:100])

            for task in tasks:
                agent = task.get("agent", "")
                task_type = task.get("task_type", "")
                description = task.get("description", "")
                priority = task.get("priority", 3)

                if agent and task_type:
                    await delegate_task(
                        "openjarvis", agent, task_type,
                        {"description": description, "boss_command": command[:200]},
                        priority=priority,
                    )
                    logger.info("Boss -> %s: %s (p%d) — %s",
                                agent, task_type, priority, description[:80])

            await db_execute(
                """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
                   VALUES (%s, 'boss_command', %s, %s, %s)""",
                ("openjarvis", json.dumps({"command": command}),
                 json.dumps({"tasks": tasks}),
                 f"Decomposed into {len(tasks)} tasks: {reasoning[:200]}"),
            )

        except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.error("Boss command handling failed: %s", exc)
            from shared.comms import send_alert
            await send_alert(
                f"Boss couldn't process command: {command[:100]}. Error: {exc}",
                sender="openjarvis",
            )

    # ── Followup Loop (boss-specific) ─────────────────────────────────

    async def _followup_loop(self):
        """Monitor team progress and intervene when needed.

        When ANATOMY_GENERATOR_LOOP is enabled, delegates to the generator
        version consumed via ``_run_loop``.
        """
        if _generator_loop_enabled():
            await _run_loop("followup_loop", self._followup_loop_gen())
            return

        while self._running:
            try:
                from shared.comms import ask_agent, send_alert
                from shared.db import fetch_all, fetch_val

                stale = await fetch_all(
                    """SELECT id, task_type, assigned_agent, created_at FROM task_queue
                       WHERE status = 'running'
                       AND created_at < NOW() - INTERVAL '10 minutes'
                       LIMIT 5"""
                )
                for task in stale or []:
                    agent = task.get("assigned_agent", "unknown")
                    task_type = task.get("task_type", "unknown")
                    logger.warning("Boss: task '%s' assigned to %s is stale (10+ min)",
                                   task_type, agent)

                    response = await ask_agent(
                        "openjarvis", agent,
                        f"You have task '{task_type}' running for 10+ minutes. Status?",
                        timeout=10,
                    )
                    if response:
                        logger.info("Boss followup — %s says: %s",
                                    agent, response.get("answer", "")[:150])

                for agent_name in ("titan", "clawdbot", "hermes"):
                    recent = await fetch_val(
                        """SELECT COUNT(*) FROM task_queue
                           WHERE assigned_agent = %s AND status = 'completed'
                           AND created_at > NOW() - INTERVAL '30 minutes'""",
                        (agent_name,),
                    ) or 0
                    if recent == 0:
                        from shared.comms import is_agent_alive
                        alive = await is_agent_alive(agent_name, max_age_seconds=120)
                        if not alive:
                            logger.error("Boss: %s appears down", agent_name)
                            await send_alert(
                                f"Agent {agent_name} may be down. No completions in 30min.",
                                sender="openjarvis",
                            )

            except (OSError, ValueError, KeyError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("Followup loop: %s", exc)

            await asyncio.sleep(300)

    async def _followup_loop_gen(self) -> AsyncGenerator[TickResult, None]:
        """Generator version of _followup_loop (Phase 26b-06)."""
        turn = 0
        while self._running:
            stale_count = 0
            down_agents: list[str] = []
            error_msg = ""
            try:
                from shared.comms import ask_agent, send_alert
                from shared.db import fetch_all, fetch_val

                stale = await fetch_all(
                    """SELECT id, task_type, assigned_agent, created_at FROM task_queue
                       WHERE status = 'running'
                       AND created_at < NOW() - INTERVAL '10 minutes'
                       LIMIT 5"""
                )
                for task in stale or []:
                    stale_count += 1
                    agent = task.get("assigned_agent", "unknown")
                    task_type = task.get("task_type", "unknown")
                    logger.warning("Boss: task '%s' assigned to %s is stale (10+ min)",
                                   task_type, agent)

                    response = await ask_agent(
                        "openjarvis", agent,
                        f"You have task '{task_type}' running for 10+ minutes. Status?",
                        timeout=10,
                    )
                    if response:
                        logger.info("Boss followup — %s says: %s",
                                    agent, response.get("answer", "")[:150])

                for agent_name in ("titan", "clawdbot", "hermes"):
                    recent = await fetch_val(
                        """SELECT COUNT(*) FROM task_queue
                           WHERE assigned_agent = %s AND status = 'completed'
                           AND created_at > NOW() - INTERVAL '30 minutes'""",
                        (agent_name,),
                    ) or 0
                    if recent == 0:
                        from shared.comms import is_agent_alive
                        alive = await is_agent_alive(agent_name, max_age_seconds=120)
                        if not alive:
                            down_agents.append(agent_name)
                            logger.error("Boss: %s appears down", agent_name)
                            await send_alert(
                                f"Agent {agent_name} may be down. No completions in 30min.",
                                sender="openjarvis",
                            )

            except (OSError, ValueError, KeyError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                error_msg = str(exc)
                logger.warning("Followup loop: %s", exc)

            yield TickResult(
                content=f"stale={stale_count} down={down_agents}" + (f" error={error_msg}" if error_msg else ""),
                turn_number=turn,
            )
            turn += 1
            await asyncio.sleep(300)

    # ── Memory Bus Subscriber Loop ───────────────────────────────────

    async def _memory_bus_loop(self) -> None:
        """Poll for memory.changed events and feed them to MAGMA.

        On startup, runs catch_up() to ingest anything missed while the
        orchestrator was down.  Then polls the events table every 5 seconds
        for new memory.changed events, dispatches each to the
        MagmaMemorySubscriber, and acknowledges processed rows.
        """
        from shared.comms import MEMORY_CHANGED_TOPIC

        try:
            from shared.magma import get_memory_subscriber
            subscriber = get_memory_subscriber()
        except (ImportError, RuntimeError) as exc:
            logger.warning("Memory bus subscriber unavailable: %s", exc)
            return

        # Catch-up on missed events from previous downtime
        try:
            caught_up = await subscriber.catch_up()
            if caught_up:
                logger.info("Memory bus: caught up %d missed records", caught_up)
        except (ConnectionError, RuntimeError, OSError) as exc:
            logger.debug("Memory bus catch-up failed (non-fatal): %s", exc)

        logger.info("Memory bus subscriber started — polling for %s events", MEMORY_CHANGED_TOPIC)
        last_seen_id = 0

        while self._running:
            try:
                from shared.db import execute, fetch_all
                rows = await fetch_all(
                    """SELECT id, payload FROM events
                       WHERE event_type = %s AND acknowledged = FALSE AND id > %s
                       ORDER BY id ASC LIMIT 50""",
                    (MEMORY_CHANGED_TOPIC, last_seen_id),
                )
                for row in rows:
                    event_id = row["id"]
                    payload = row.get("payload", {})
                    if isinstance(payload, str):
                        import json as _json
                        try:
                            payload = _json.loads(payload)
                        except (ValueError, TypeError):
                            payload = {}

                    try:
                        await subscriber.handle_event(payload)
                    except (RuntimeError, OSError, ConnectionError) as exc:
                        logger.debug("Memory bus: handle_event failed for %s: %s", event_id, exc)

                    # Acknowledge regardless (avoid infinite retry on bad payloads)
                    await execute(
                        "UPDATE events SET acknowledged = TRUE WHERE id = %s",
                        (event_id,),
                    )
                    last_seen_id = max(last_seen_id, event_id)

            except (ConnectionError, RuntimeError, OSError) as exc:
                logger.debug("Memory bus poll error (will retry): %s", exc)

            await asyncio.sleep(5)

    # ── Nightly Sleep Cycle ───────────────────────────────────────────

    async def _sleep_cycle_loop(self):
        """Run nightly self-optimization at ~2 AM."""
        while self._running:
            now = datetime.datetime.now()
            target = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logger.info("Sleep cycle scheduled in %.0f hours", wait_seconds / 3600)

            # Sleep until target (check running flag periodically)
            while self._running and wait_seconds > 0:
                sleep_chunk = min(wait_seconds, 60)
                await asyncio.sleep(sleep_chunk)
                wait_seconds -= sleep_chunk

            if not self._running:
                break

            await self._run_sleep_cycle()

    async def _run_sleep_cycle(self):
        """Execute the nightly Alpha/Beta self-optimization cycle."""
        logger.info("Starting nightly sleep cycle...")
        try:
            from openjarvis.vassals.sleep_cycle import run_sleep_cycle
            await run_sleep_cycle()
            logger.info("Sleep cycle completed")

            # Also evaluate cell division after sleep cycle
            from openjarvis.vassals.cell_division import evaluate_division_need
            from shared.pipeline import assess_pipeline_state
            state = await assess_pipeline_state()
            cycle_id = datetime.datetime.now().strftime("%Y-%m-%d")
            await evaluate_division_need(state, cycle_id)

        except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.error("Sleep cycle error: %s", exc, exc_info=True)

    # ── Codebase Self-Audit ────────────────────────────────────────────

    async def _self_audit_loop(self):
        """Run codebase self-audit every 3 hours with multi-agent consensus.

        When ANATOMY_GENERATOR_LOOP is enabled, delegates to the generator
        version consumed via ``_run_loop``.
        """
        if _generator_loop_enabled():
            # Initial delay preserved
            await asyncio.sleep(1800)
            await _run_loop("self_audit_loop", self._self_audit_loop_gen())
            return

        # Initial delay: wait 30 min after startup for system to stabilize
        await asyncio.sleep(1800)

        while self._running:
            try:
                from perseus.self_audit import run_self_audit
                result = await run_self_audit()
                logger.info(
                    "Self-audit: %d findings, %d approved, %d applied",
                    result.get("total_findings", 0),
                    result.get("approved", 0),
                    result.get("applied", 0),
                )
            except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("Self-audit failed: %s", exc)

            # Wait 3 hours until next cycle
            wait = 10800  # 3 hours
            while self._running and wait > 0:
                chunk = min(wait, 60)
                await asyncio.sleep(chunk)
                wait -= chunk

    async def _self_audit_loop_gen(self) -> AsyncGenerator[TickResult, None]:
        """Generator version of _self_audit_loop (Phase 26b-06)."""
        turn = 0
        while self._running:
            findings = 0
            error_msg = ""
            try:
                from perseus.self_audit import run_self_audit
                result = await run_self_audit()
                findings = result.get("total_findings", 0)
                logger.info(
                    "Self-audit: %d findings, %d approved, %d applied",
                    findings,
                    result.get("approved", 0),
                    result.get("applied", 0),
                )
            except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                error_msg = str(exc)
                logger.warning("Self-audit failed: %s", exc)

            yield TickResult(
                content=f"findings={findings}" + (f" error={error_msg}" if error_msg else ""),
                turn_number=turn,
            )
            turn += 1

            # Wait 3 hours until next cycle
            wait = 10800  # 3 hours
            while self._running and wait > 0:
                chunk = min(wait, 60)
                await asyncio.sleep(chunk)
                wait -= chunk

    # ── Ruflo Validation Loop ───────────────────────────────────────────

    async def _ruflo_validation_loop(self):
        """Poll completed Ruflo tasks and validate fixes via targeted self-audit."""
        from shared.db import execute, fetch_all

        # Initial delay: let Ruflo come online
        await asyncio.sleep(120)

        while self._running:
            try:
                pending = await fetch_all(
                    "SELECT * FROM ruflo_tasks "
                    "WHERE status = 'completed' AND validation_status IS NULL "
                    "ORDER BY completed_at ASC LIMIT 5"
                )
                for task in pending:
                    task_id = task["id"]
                    task_type = task.get("task_type", "")
                    result = task.get("result", {})
                    changed_files = result.get("changed_files", []) if isinstance(result, dict) else []

                    if not changed_files:
                        # No files changed — mark as skipped
                        await execute(
                            "UPDATE ruflo_tasks SET validation_status = 'skipped', validated_at = NOW() "
                            "WHERE id = %s", (task_id,)
                        )
                        continue

                    # Run targeted self-audit on changed files
                    validation_passed = True
                    validation_details = {}
                    try:
                        from perseus.self_audit import _analyze_file
                        for fpath in changed_files[:10]:  # cap at 10 files
                            full = Path(config.root_dir) / fpath if hasattr(config, "root_dir") else Path(fpath)
                            if full.exists():
                                findings = await _analyze_file(str(fpath), full.read_text())
                                if findings:
                                    validation_details[fpath] = [f.get("issue", "") for f in findings]
                                    validation_passed = False
                    except (ImportError, OSError, ValueError, KeyError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
                        logger.debug("Ruflo validation analysis failed: %s", e)
                        validation_details["error"] = str(e)

                    status = "passed" if validation_passed else "failed"
                    await execute(
                        "UPDATE ruflo_tasks SET validation_status = %s, "
                        "validation_details = %s, validated_at = NOW() WHERE id = %s",
                        (status, json.dumps(validation_details), task_id),
                    )

                    # Emit learning event
                    from shared.db import emit_event
                    event_type = "ruflo_fix_validated" if validation_passed else "ruflo_fix_rejected"
                    await emit_event(event_type, {
                        "task_id": task_id,
                        "task_type": task_type,
                        "changed_files": changed_files,
                        "validation_status": status,
                    })

                    # Store learning
                    from shared.comms import store_learning
                    if validation_passed:
                        await store_learning(
                            category="ruflo_code_pattern",
                            insight=f"Ruflo successfully fixed {task_type} in {', '.join(changed_files[:3])}",
                            confidence=0.7,
                            source_agent="ruflo",
                            source_event="ruflo_fix_validated",
                        )
                    else:
                        await store_learning(
                            category="ruflo_code_pattern",
                            insight=f"Ruflo fix rejected for {task_type}: {json.dumps(validation_details)[:200]}",
                            confidence=0.3,
                            source_agent="ruflo",
                            source_event="ruflo_fix_rejected",
                        )

                    logger.info("Ruflo task %d validation: %s (%s)", task_id, status, task_type)

            except (OSError, ValueError, KeyError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.debug("Ruflo validation loop error: %s", exc)

            # Check every 60 seconds
            await asyncio.sleep(60)

    # ── External Intelligence Scout ────────────────────────────────────

    async def _scout_loop(self):
        """Run external intelligence scout 2x daily (8 AM and 6 PM)."""
        while self._running:
            now = datetime.datetime.now()
            # Next target: 8:17 AM or 6:43 PM (off-minute to avoid fleet collisions)
            targets = [
                now.replace(hour=8, minute=17, second=0, microsecond=0),
                now.replace(hour=18, minute=43, second=0, microsecond=0),
            ]
            # Find next future target
            future_targets = [t for t in targets if t > now]
            if not future_targets:
                # Both passed today — schedule first one tomorrow
                target = targets[0] + datetime.timedelta(days=1)
            else:
                target = future_targets[0]

            wait_seconds = (target - now).total_seconds()
            is_morning = target.hour < 12

            logger.info("Scout cycle scheduled in %.1f hours (%s)", wait_seconds / 3600,
                         "morning" if is_morning else "evening")

            while self._running and wait_seconds > 0:
                sleep_chunk = min(wait_seconds, 60)
                await asyncio.sleep(sleep_chunk)
                wait_seconds -= sleep_chunk

            if not self._running:
                break

            try:
                from perseus.scout import run_scout_cycle
                result = await run_scout_cycle(include_tier2=is_morning)
                logger.info("Scout cycle: %d found, %d new, %d actionable",
                            result.get("total", 0), result.get("new", 0), result.get("actionable", 0))
            except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("Scout cycle failed: %s", exc)

    async def _deerflow_loop(self):
        """Run DeerFlow-style continuous research and a daily evolution brief."""
        last_cycle_at = 0.0
        last_paper_scan_at = 0.0
        last_repo_scan_at = 0.0
        last_brief_date: datetime.date | None = None

        while self._running:
            now = datetime.datetime.now()
            now_ts = time.time()

            try:
                from shared.oj_bridge import call_agent_async

                if now_ts - last_cycle_at >= 15 * 60:
                    result = await call_agent_async(
                        "deerflow_research",
                        "evolution_research_cycle",
                        {
                            "topic": (
                                "Continuously research how Perseus should evolve using "
                                "Stanford papers, arXiv, and GitHub."
                            ),
                            "mode": "continuous",
                            "sources": ["stanford", "arxiv", "github"],
                        },
                        timeout=90.0,
                    )
                    if "error" not in result:
                        last_cycle_at = now_ts
                        logger.info("DeerFlow cycle completed: %s", result.get("cycle_id", "unknown"))

                if now_ts - last_paper_scan_at >= 60 * 60:
                    result = await call_agent_async(
                        "deerflow_research",
                        "paper_scan",
                        {
                            "topic": "New Stanford and arXiv papers relevant to Hermes, Titan, ClawdBot, OpenJarvis, and local inference.",
                            "sources": ["stanford", "arxiv"],
                        },
                        timeout=90.0,
                    )
                    if "error" not in result:
                        last_paper_scan_at = now_ts

                if now_ts - last_repo_scan_at >= 60 * 60:
                    result = await call_agent_async(
                        "deerflow_research",
                        "repo_scan",
                        {
                            "topic": "Trending and newly released GitHub repos Perseus should steal from.",
                            "sources": ["github"],
                        },
                        timeout=90.0,
                    )
                    if "error" not in result:
                        last_repo_scan_at = now_ts

                if now.hour == 23 and now.minute >= 40 and last_brief_date != now.date():
                    result = await call_agent_async(
                        "deerflow_research",
                        "daily_evolution_brief",
                        {"lookback_hours": 24},
                        timeout=90.0,
                    )
                    if "error" not in result:
                        last_brief_date = now.date()
                        logger.info("DeerFlow daily brief generated: %s", result.get("artifact", {}))
            except (ImportError, OSError, RuntimeError, ValueError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.debug("DeerFlow loop error: %s", exc)

            await asyncio.sleep(60)

    # ── Weekly Maintenance (Ruflo) ─────────────────────────────────────

    async def _ruflo_maintenance_loop(self):
        """Weekly scheduled maintenance tasks dispatched to Ruflo.

        Monday 3 AM: dependency_audit
        Wednesday 3 AM: security_scan
        Friday 3 AM: code_refactor (complexity analysis)
        """
        import datetime as _dt

        from shared.comms import request_task

        # Wait for system to stabilize
        await asyncio.sleep(3600)

        # Map: weekday (0=Mon) → task type
        schedule = {
            0: "dependency_audit",   # Monday
            2: "security_scan",      # Wednesday
            4: "code_refactor",      # Friday
        }

        while self._running:
            now = _dt.datetime.now()
            weekday = now.weekday()
            task_type = schedule.get(weekday)

            if task_type and now.hour == 3 and now.minute < 5:
                try:
                    task_id = await request_task(task_type, {
                        "capability": task_type,
                        "source": "maintenance",
                        "source_id": f"maint-{now.strftime('%Y%m%d')}-{task_type}",
                        "scope": "full_codebase",
                    }, dedupe=True)
                    if task_id:
                        logger.info("Weekly maintenance: dispatched %s to Ruflo (task %s)",
                                   task_type, task_id)
                except (OSError, ValueError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.debug("Weekly maintenance dispatch failed: %s", e)

            # Check every 5 minutes
            await asyncio.sleep(300)

    # ── Shutdown ──────────────────────────────────────────────────────

    def _request_shutdown(self):
        """Signal handler — request graceful shutdown."""
        logger.info("Shutdown signal received — stopping OpenJarvis...")
        self._running = False
        # Stop managed components
        if self._scheduler:
            asyncio.ensure_future(self._scheduler.stop())
        if self._relay:
            asyncio.ensure_future(self._relay.stop())

    async def _cleanup(self):
        """Graceful cleanup on shutdown."""
        logger.info("Cleaning up...")

        # Stop vassals
        if self._supervisor:
            try:
                await asyncio.wait_for(self._supervisor.stop_all(), timeout=15.0)
                logger.info("All vassals stopped")
            except TimeoutError:
                logger.warning("Vassal shutdown timed out")

        # Close DB pool
        try:
            from shared.db import close_pool
            await close_pool()
            logger.info("DB pool closed")
        except (OSError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            pass

        if self._oj_system is not None:
            try:
                if self._learning_handler is not None:
                    from openjarvis.core.events import EventType

                    self._oj_system.bus.unsubscribe(
                        EventType.AGENT_LEARNING_STARTED,
                        self._learning_handler,
                    )
                self._oj_system.close()
                logger.info("OpenJarvis system closed")
            except (OSError, RuntimeError, AttributeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("OpenJarvis system cleanup failed", exc_info=True)

        logger.info("OpenJarvis shutdown complete")


# ── Entry Points ──────────────────────────────────────────────────────

def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    orchestrator = Orchestrator()
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("OpenJarvis stopped")
    except Exception as exc:
        capture_exception(exc, service_name="orchestrator", category="main")
        logger.exception("OpenJarvis crashed")
        raise


async def main_with_a2a():
    """Entry point for Orchestrator + A2A server."""
    import uvicorn

    from shared.a2a_wrapper import AgentCard, create_a2a_app

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    orchestrator = Orchestrator()
    install_asyncio_exception_handler(asyncio.get_running_loop(), "orchestrator")

    card = AgentCard(
        name="openjarvis",
        description=(
            "OpenJarvis — THE boss. Strategic allocation, vassal lifecycle, "
            "pipeline orchestration, budget enforcement, self-optimization."
        ),
        capabilities=[
            "health_check", "operator_command", "pipeline_state",
            "vassal_status", "vassal_restart", "scheduler_status",
            "budget_report", "sleep_cycle_trigger", "ask",
        ],
    )
    a2a_app = create_a2a_app(
        agent_card=card,
        handler=orchestrator.handle_a2a,
    )

    a2a_port = int(os.environ.get("ORCHESTRATOR_A2A_PORT", "9000"))
    uvi_config = uvicorn.Config(
        a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning",
    )
    server = uvicorn.Server(uvi_config)

    logger.info("OpenJarvis A2A server starting on :%d", a2a_port)

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, orchestrator._request_shutdown)

    try:
        await asyncio.gather(orchestrator.start(), server.serve())
    except Exception as exc:
        capture_exception(exc, service_name="orchestrator", category="a2a")
        logger.exception("OpenJarvis A2A runtime crashed")
        raise


if __name__ == "__main__":
    # Phase 18b-06: fast-path for lightweight queries — no full boot needed
    if _fast_path_dispatch():
        sys.exit(0)

    if os.environ.get("ORCHESTRATOR_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        main()
