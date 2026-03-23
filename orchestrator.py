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
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from shared.logging_config import setup_logging
from shared.observability import capture_exception, install_asyncio_exception_handler

logger = setup_logging("orchestrator")

# Vassal A2A endpoints
VASSAL_CONFIG = {
    "titan": {"url": f"http://localhost:{os.environ.get('TITAN_A2A_PORT', '9001')}"},
    "hermes": {"url": f"http://localhost:{os.environ.get('HERMES_A2A_PORT', '9002')}"},
    "clawdbot": {"url": f"http://localhost:{os.environ.get('CLAWDBOT_A2A_PORT', '9003')}"},
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
            "instruction": (
                "Act as OpenJarvis's strategic reasoning core. Investigate system issues, "
                "inspect runtime state, and produce actionable recommendations for the boss."
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
            "instruction": (
                "Use ReAct to inspect logs, search for failures, and gather concrete evidence "
                "about runtime regressions before escalation."
            ),
            "tools": [
                "think", "file_read", "shell_exec", "memory_store",
                "memory_retrieve", "memory_search",
            ],
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
            "instruction": (
                "Monitor OpenJarvis runtime health, tool failures, scheduler drift, and agent "
                "activity. Persist state between ticks and escalate actionable anomalies."
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
            except Exception as exc:
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

        # 1. Boot OJ runtime
        from shared.oj_bridge import (
            get_bus,
            get_trace_store,
            get_agent_manager,
            get_audit_logger,
        )
        bus = get_bus()
        get_trace_store()
        get_agent_manager()
        get_audit_logger()

        from shared.db import init_pool
        await init_pool()
        logger.info("OJ runtime initialized")

        # Boot the full OpenJarvis framework so production actually uses
        # managed agents, tools, operators, routing, and learning hooks.
        await self._bootstrap_openjarvis_framework(bus)

        # 2. Start vassals (with port-probe reconciliation)
        project_dir = os.path.dirname(os.path.abspath(__file__))
        from openjarvis.vassals.supervisor import VassalSupervisor
        self._supervisor = VassalSupervisor(bus, project_dir)
        self._supervisor.register_defaults(project_dir)
        await self._probe_and_start_vassals()
        logger.info("Vassal supervisor ready")

        # Wait for A2A servers to come up
        await asyncio.sleep(3)

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
        from openjarvis.vassals.perseus_scheduler import PerseusScheduler, PerseusConfig

        sched_config = PerseusConfig(
            tick_interval=int(os.environ.get("SCHEDULER_TICK_INTERVAL", "60")),
            budget_monthly_cap=float(os.environ.get("MONTHLY_BUDGET_CAP", "800")),
        )

        budget_guard = None
        try:
            from tools.budget_guard import BudgetGuard
            budget_guard = BudgetGuard()
        except Exception:
            pass

        self._scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=self._discovery,
            config=sched_config,
            budget_guard=budget_guard,
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
            await asyncio.gather(
                self._scheduler.start(),       # Strategic brain
                self._relay.start_polling(),   # Event bridge
                self._command_loop(),          # Operator commands
                self._followup_loop(),         # Stale task monitoring
                self._sleep_cycle_loop(),      # Nightly optimization
                self._scout_loop(),            # External intelligence
                self._self_audit_loop(),       # Codebase self-audit
            )
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
            except Exception:
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
        except Exception as exc:
            logger.warning("OpenJarvis framework bootstrap failed: %s", exc, exc_info=True)

    def _ensure_framework_agents(self):
        """Create and schedule baseline managed OpenJarvis agents."""
        if not self._oj_system or not self._oj_system.agent_manager:
            return

        manager = self._oj_system.agent_manager
        scheduler = self._oj_system.agent_scheduler
        existing = {agent["name"]: agent for agent in manager.list_agents()}

        for spec in FRAMEWORK_AGENT_SPECS:
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
            except Exception as exc:
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
                except Exception as exc:
                    logger.warning("Learning run failed for %s: %s", agent_id, exc)

            threading.Thread(target=_run, daemon=True, name=f"oj-learning-{agent_id[:6]}").start()

        return _handle_learning

    # ── Operator Command Loop (boss-specific) ─────────────────────────

    async def _command_loop(self):
        """Listen for operator commands and decompose into agent work."""
        while self._running:
            try:
                from shared.db import fetch_all, execute as db_execute

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

            except Exception as exc:
                logger.warning("Command loop: %s", exc)

            await asyncio.sleep(10)

    async def _handle_command(self, command: str):
        """Decompose a high-level operator command into agent tasks via LLM."""
        from shared.llm_client import llm
        from shared.comms import delegate_task
        from shared.db import execute as db_execute

        logger.info("Boss received command: %s", command[:100])

        try:
            plan = await llm.generate(
                f"You are OpenJarvis, the boss of a 3-agent business team:\n"
                f"- Titan: revenue pipeline (lead discovery, email outreach, deals, invoicing, payments)\n"
                f"- ClawdBot: skills executor (browser automation, web scraping, site building, research, image gen)\n"
                f"- Hermes: operator comms (Telegram alerts, dashboard, briefings)\n\n"
                f"The operator commands: \"{command}\"\n\n"
                f"Decompose this into specific, actionable tasks for your agents.\n"
                f"Available task_types for Titan: lead_discovery, lead_research, email_compose, email_send, "
                f"follow_up_check, close_interested, build_sites, process_invoices, sync_analytics\n"
                f"Available task_types for ClawdBot: skill_execute, web_scrape, browser_task, enrich_lead, "
                f"site_verify, image_generation, capability_resolve\n"
                f"Available task_types for Hermes: send_alert, morning_briefing\n\n"
                f"Return ONLY valid JSON:\n"
                f"{{\"tasks\": [{{\"agent\": \"titan|clawdbot|hermes\", \"task_type\": \"...\", "
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

        except Exception as exc:
            logger.error("Boss command handling failed: %s", exc)
            from shared.comms import send_alert
            await send_alert(
                f"Boss couldn't process command: {command[:100]}. Error: {exc}",
                sender="openjarvis",
            )

    # ── Followup Loop (boss-specific) ─────────────────────────────────

    async def _followup_loop(self):
        """Monitor team progress and intervene when needed."""
        while self._running:
            try:
                from shared.db import fetch_all, fetch_val
                from shared.comms import ask_agent, send_alert

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

            except Exception as exc:
                logger.warning("Followup loop: %s", exc)

            await asyncio.sleep(300)

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

        except Exception as exc:
            logger.error("Sleep cycle error: %s", exc, exc_info=True)

    # ── Codebase Self-Audit ────────────────────────────────────────────

    async def _self_audit_loop(self):
        """Run codebase self-audit every 3 hours with multi-agent consensus."""
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
            except Exception as exc:
                logger.warning("Self-audit failed: %s", exc)

            # Wait 3 hours until next cycle
            wait = 10800  # 3 hours
            while self._running and wait > 0:
                chunk = min(wait, 60)
                await asyncio.sleep(chunk)
                wait -= chunk

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
            except Exception as exc:
                logger.warning("Scout cycle failed: %s", exc)

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
            except asyncio.TimeoutError:
                logger.warning("Vassal shutdown timed out")

        # Close DB pool
        try:
            from shared.db import close_pool
            await close_pool()
            logger.info("DB pool closed")
        except Exception:
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
            except Exception:
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
    if os.environ.get("ORCHESTRATOR_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        main()
