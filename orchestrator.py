"""OpenJarvis Orchestrator — replaces Perseus as the system brain.

Thin process that:
- Boots the OJ runtime (EventBus, TraceStore, AgentManager, AuditLogger)
- Runs the revenue pipeline via WorkflowEngine on a schedule
- Monitors agent health via A2A endpoints
- Enforces budget via EventBus
- Does NOT execute pipeline stages (Titan owns execution)

Usage:
    python orchestrator.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time

logger = logging.getLogger("perseus.orchestrator")


class Orchestrator:
    """The OJ-native orchestrator that replaces Perseus."""

    def __init__(self):
        self._running = False
        self._pipeline_interval = int(os.environ.get("PIPELINE_INTERVAL", "300"))  # 5 min
        self._health_interval = int(os.environ.get("HEALTH_INTERVAL", "60"))  # 1 min
        self._budget_interval = int(os.environ.get("BUDGET_INTERVAL", "120"))  # 2 min

    async def start(self):
        """Start the orchestrator."""
        self._running = True
        logger.info("Orchestrator starting...")

        # Boot OJ runtime
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

        logger.info("OJ runtime initialized")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, self._shutdown)

        # Run concurrent loops
        await asyncio.gather(
            self._pipeline_loop(),
            self._health_loop(),
            self._budget_loop(),
        )

    def _shutdown(self):
        logger.info("Shutdown signal received")
        self._running = False

    async def _pipeline_loop(self):
        """Run the revenue pipeline on interval."""
        while self._running:
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
