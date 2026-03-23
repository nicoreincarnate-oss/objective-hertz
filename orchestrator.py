"""OpenJarvis Orchestrator — THE system brain. Replaces Perseus entirely.

Responsibilities:
- Boots the OJ runtime (EventBus, TraceStore, AgentManager, AuditLogger)
- Runs the revenue pipeline via WorkflowEngine on a schedule
- Strategic priority allocation (absorbed from Perseus)
- Monitors agent health via A2A endpoints
- Enforces budget (fiat + Conway crypto)
- Monitors Conway survival tiers
- Does NOT execute pipeline stages (Titan owns execution)

Usage:
    python orchestrator.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time

logger = logging.getLogger("openjarvis.orchestrator")


class Orchestrator:
    """The sole orchestrator — boss of Titan, Hermes, and ClawdBot."""

    def __init__(self):
        self._running = False
        self._pipeline_interval = int(os.environ.get("PIPELINE_INTERVAL", "300"))  # 5 min
        self._health_interval = int(os.environ.get("HEALTH_INTERVAL", "60"))  # 1 min
        self._budget_interval = int(os.environ.get("BUDGET_INTERVAL", "120"))  # 2 min
        self._strategic_interval = 60  # 60s strategic tick (from Perseus)
        self._last_run: dict[str, float] = {}

    async def start(self):
        """Start the orchestrator."""
        self._running = True
        logger.info("OpenJarvis Orchestrator starting — I am the boss now.")

        # Boot OJ runtime
        from shared.oj_bridge import (
            get_bus,
            get_trace_store,
            get_agent_manager,
            get_audit_logger,
        )
        get_bus()
        get_trace_store()
        get_agent_manager()
        get_audit_logger()

        # Initialize DB
        from shared.db import init_pool
        await init_pool()

        logger.info("OJ runtime initialized")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, self._shutdown)

        # Run concurrent loops (all formerly split between Perseus + Orchestrator)
        loops = [
            self._pipeline_loop(),
            self._health_loop(),
            self._budget_loop(),
            self._strategic_loop(),
        ]

        # Add Conway survival monitoring if enabled
        from shared.config import config
        if config.conway.enabled:
            loops.append(self._conway_survival_loop())

        await asyncio.gather(*loops)

    def _shutdown(self):
        logger.info("Shutdown signal received")
        self._running = False

    async def _pipeline_loop(self):
        """Run the revenue pipeline on interval."""
        while self._running:
            # Check for agent recommendations
            try:
                from shared.comms import get_pending_recommendations
                recs = await get_pending_recommendations("perseus", since_minutes=30, limit=5)
                for rec in recs:
                    payload = rec.get("payload", {})
                    if isinstance(payload, str):
                        import json
                        payload = json.loads(payload)
                    logger.info(f"Recommendation from {payload.get('from', '?')}: {payload.get('topic', '')}")
            except Exception:
                pass

            try:
                from titan.workflow_pipeline import run_pipeline
                logger.info("Starting pipeline cycle...")
                result = run_pipeline()
                if not result.success:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_warning", {
                        "text": f"Pipeline cycle had failures: {[s.node_id for s in result.steps if not s.success]}",
                    })
            except Exception as exc:
                logger.error("Pipeline cycle error: %s", exc, exc_info=True)
                try:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_urgent", {
                        "text": f"Pipeline cycle crashed: {exc}",
                    })
                except Exception:
                    pass

            await asyncio.sleep(self._pipeline_interval)

    async def _health_loop(self):
        """Check agent health via A2A endpoints."""
        while self._running:
            try:
                from shared.oj_bridge import call_agent, AGENT_URLS

                statuses = {}
                for agent_name in AGENT_URLS:
                    result = call_agent(agent_name, "health_check")
                    status = result.get("status", "unknown")
                    statuses[agent_name] = status
                    if status not in ("running", "ok"):
                        logger.warning("Agent %s health: %s", agent_name, status)

                # Publish health to EventBus
                from shared.oj_bridge import get_bus
                from openjarvis.core.events import EventType
                get_bus().publish(EventType.A2A_REMOTE_EVENT, {
                    "event_type": "health_report",
                    "agents": statuses,
                })

            except Exception as exc:
                logger.error("Health check error: %s", exc)

            await asyncio.sleep(self._health_interval)

    async def _budget_loop(self):
        """Enforce budget limits."""
        while self._running:
            try:
                from tools.budget_guard import BudgetGuard
                guard = BudgetGuard()
                budget = await guard.check_budget()

                if budget.get("exceeded"):
                    logger.warning("Budget exceeded — pausing agents")
                    from shared import db
                    await db.set_config("titan_paused", True)
                    await db.set_config("clawdbot_paused", True)

                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_urgent", {
                        "text": f"Budget exceeded: ${budget.get('total_spent', 0):.0f}/${budget.get('cap', 800)}",
                    })

                elif budget.get("percent_used", 0) >= 80:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_warning", {
                        "text": f"Budget at {budget.get('percent_used', 0):.0f}%",
                    })

            except Exception as exc:
                logger.error("Budget check error: %s", exc)

            await asyncio.sleep(self._budget_interval)

    async def _strategic_loop(self):
        """Strategic priority allocation — absorbed from Perseus.

        Assesses pipeline state every 60s and decides which tasks to
        schedule next. Uses deterministic rules (no LLM), prioritizing
        revenue-closest work first.
        """
        while self._running:
            try:
                from shared.db import execute as db_execute, insert_task
                from shared.pipeline import assess_pipeline_state

                state = await assess_pipeline_state()
                priorities = _decide_priorities(state)

                # Record decision for auditability
                try:
                    await db_execute(
                        """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
                           VALUES (%s, 'priority_allocation', %s, %s, %s)""",
                        (
                            "orchestrator",
                            json.dumps(state),
                            json.dumps(priorities),
                            priorities.get("reasoning", ""),
                        ),
                    )
                except Exception:
                    pass

                # Schedule prioritized tasks
                now = time.time()
                for task_name in priorities.get("schedule", []):
                    elapsed = now - self._last_run.get(task_name, 0)
                    if elapsed < 30:  # minimum 30s between same task
                        continue
                    self._last_run[task_name] = now
                    task_id = await insert_task(
                        task_name,
                        {"scheduled": True, "priority_reason": priorities.get("reasoning", "")},
                    )
                    if task_id:
                        logger.info(f"Scheduled: {task_name} — {priorities.get('reasoning', '')[:80]}")

            except Exception as exc:
                logger.debug(f"Strategic tick: {exc}")

            await asyncio.sleep(self._strategic_interval)

    async def _conway_survival_loop(self):
        """Monitor agent survival tiers (Conway economic system)."""
        while self._running:
            try:
                from conway.wallet import WalletManager
                from conway.ledger import EconomicLedger
                from conway.survival import SurvivalMonitor

                wm = WalletManager()
                ledger = EconomicLedger()
                monitor = SurvivalMonitor(wm, ledger)

                results = await monitor.check_all_agents()
                for r in results:
                    if r.get("changed"):
                        logger.warning(
                            f"Conway tier change: {r['agent']} → {r['tier']} "
                            f"(balance: ${r['balance']:.2f})"
                        )
            except Exception as exc:
                logger.debug(f"Conway survival check: {exc}")

            await asyncio.sleep(120)  # Check every 2 min


# ── Strategic priority logic (from Perseus, deterministic) ─────

def _decide_priorities(state: dict) -> dict:
    """
    Deterministic priority logic — no LLM call, just pipeline math.
    Rule: always prioritize revenue-closest work first.
    """
    schedule_now: list[str] = []
    skip_now: list[str] = []
    reasons: list[str] = []

    hot = state.get("hot_leads", 0)
    ready_close = state.get("ready_to_close", 0)
    ready_deliver = state.get("ready_to_deliver", 0)
    ready_invoice = state.get("ready_to_invoice", 0)
    outreach = state.get("outreach_active", 0)

    # Priority 1: Invoices waiting → money on the table
    if ready_invoice > 0:
        schedule_now.append("process_invoices")
        reasons.append(f"{ready_invoice} leads ready to invoice")

    # Priority 2: Sites to build → unblock invoicing
    if ready_deliver > 0:
        schedule_now.append("build_sites")
        schedule_now.append("site_verify")
        reasons.append(f"{ready_deliver} sites to build")

    # Priority 3: Hot leads → close before they cool
    if hot > 0 or ready_close > 0:
        schedule_now.append("close_interested")
        schedule_now.append("follow_up_check")
        reasons.append(f"{hot} hot leads, {ready_close} ready to close")

    # Priority 4: Active outreach needs analytics + follow-ups
    if outreach > 0:
        schedule_now.append("sync_analytics")
        schedule_now.append("email_send")
        schedule_now.append("email_compose")
        schedule_now.append("follow_up_check")

    # Priority 5: Top of funnel — only if not overwhelmed downstream
    if hot <= 3 and ready_deliver <= 2:
        schedule_now.append("lead_discovery")
        schedule_now.append("lead_research")
        schedule_now.append("enrich_leads")
    else:
        skip_now.extend(["lead_discovery", "lead_research", "enrich_leads"])
        reasons.append(f"Skipping discovery: {hot} hot + {ready_deliver} to deliver")

    # Deduplicate preserving order
    seen: set[str] = set()
    deduped = [n for n in schedule_now if n not in seen and not seen.add(n)]

    reasoning = "; ".join(reasons) if reasons else "Normal pipeline flow"
    return {"schedule": deduped, "skip": skip_now, "reasoning": reasoning}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    orchestrator = Orchestrator()
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped")


if __name__ == "__main__":
    main()
