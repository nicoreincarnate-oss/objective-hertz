"""
Perseus Master Daemon — The brain that coordinates everything.
Runs 24/7, manages schedules, monitors health, enforces budget.
"""

import asyncio
import signal
import time
import logging

from shared.config import config
from shared.logging_config import setup_logging
from shared import db
from shared.agent_base import AgentBase

from perseus.scheduler import SCHEDULES
from perseus.agent_registry import heartbeat, check_agent_health

logger = setup_logging("perseus")


class PerseusDaemon(AgentBase):
    name = "perseus"
    description = "Master AI orchestrator — coordinates all agents, manages schedules and budget."

    def __init__(self):
        super().__init__()
        self._running = False
        self._last_run: dict[str, float] = {}

    async def start(self):
        """Start Perseus master loop."""
        logger.info("=" * 60)
        logger.info("PERSEUS — Master AI System Starting")
        logger.info("=" * 60)

        await db.init_pool()
        await self.register()
        self._running = True

        # Initialize last_run times
        now = time.time()
        for sched in SCHEDULES:
            self._last_run[sched.name] = now

        logger.info(f"Loaded {len(SCHEDULES)} schedules")
        logger.info("Perseus is LIVE.")

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Perseus tick error: {e}", exc_info=True)
            await asyncio.sleep(10)  # Check every 10 seconds

    async def stop(self):
        """Gracefully stop Perseus."""
        logger.info("Perseus shutting down...")
        self._running = False
        await self.deregister()
        await db.close_pool()
        logger.info("Perseus stopped.")

    async def health_check(self) -> dict:
        agents = await check_agent_health()
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "managed_agents": agents,
        }

    async def _tick(self):
        """One tick of the master loop — check if any schedules are due."""
        now = time.time()
        await heartbeat(self.name)

        for sched in SCHEDULES:
            elapsed = now - self._last_run.get(sched.name, 0)
            if elapsed >= sched.interval_seconds:
                self._last_run[sched.name] = now
                logger.debug(f"Scheduling: {sched.name}")
                # Insert task into queue for Titan/Hermes to pick up
                await db.insert_task(sched.name, {"scheduled": True})

        # Budget enforcement
        await self._enforce_budget()

    async def _enforce_budget(self):
        """Check budget and enforce cap."""
        try:
            from tools.budget_guard import BudgetGuard
            guard = BudgetGuard()
            status = await guard.check_budget()
            if status.get("exceeded"):
                logger.warning("BUDGET EXCEEDED — pausing non-essential operations")
                await self.emit_event("budget_exceeded", status)
        except (ImportError, Exception):
            pass  # Budget guard not available yet


async def main():
    """Entry point for Perseus daemon."""
    perseus = PerseusDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(perseus.stop()))

    await perseus.start()


if __name__ == "__main__":
    asyncio.run(main())
