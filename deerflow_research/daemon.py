"""Continuous research daemon backed by the upstream ByteDance DeerFlow engine."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

from openjarvis.vassals.registry import heartbeat
from shared import db
from shared.agent_base import AgentBase
from shared.logging_config import setup_logging

from .runtime import DeerFlowResearchRuntime

logger = setup_logging("deerflow_research")


class DeerFlowResearchDaemon(AgentBase):
    name = "deerflow_research"
    description = (
        "Continuous research daemon for messy, open-ended evolution scouting, "
        "paper monitoring, repo monitoring, and daily improvement briefs."
    )

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._started_at = 0.0
        self._idle_interval = int(os.environ.get("DEERFLOW_RESEARCH_HEARTBEAT_SECONDS", "45"))
        self._runtime = DeerFlowResearchRuntime()

    async def start(self) -> None:
        logger.info("DeerFlow research daemon starting...")
        try:
            await db.init_pool()
        except Exception as exc:
            logger.debug("DeerFlow DB init skipped: %s", exc)
        await self.register()
        self._stopped.clear()
        self._running = True
        self._started_at = time.time()

        try:
            while self._running:
                self.begin_work("loop:heartbeat")
                try:
                    await heartbeat(self.name)
                except Exception as exc:
                    logger.debug("DeerFlow heartbeat failed: %s", exc)
                finally:
                    self.finish_work("loop:heartbeat")
                await asyncio.sleep(self._idle_interval)
        finally:
            await self.finalize_shutdown()

    async def stop(self) -> None:
        logger.info("DeerFlow shutdown requested...")
        self.request_shutdown()
        drained = await self.wait_for_work_drain()
        if not drained:
            logger.warning("DeerFlow shutdown with pending work")
        self._running = False
        await self.wait_until_stopped()

    async def health_check(self) -> dict[str, Any]:
        runtime = await self._runtime.health_check()
        runtime.update(
            {
                "status": "running" if self._running else "stopped",
                "uptime_seconds": round(time.time() - self._started_at, 2) if self._started_at else 0.0,
            }
        )
        return runtime

    async def evolution_research_cycle(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._emit("deerflow_cycle_started", payload)
        result = await self._runtime.evolution_research_cycle(payload)
        await self._emit("deerflow_cycle_completed", {**payload, **result})
        return result

    async def paper_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._emit("deerflow_paper_scan_started", payload)
        result = await self._runtime.paper_scan(payload)
        await self._emit("deerflow_paper_scan_completed", {**payload, **result})
        return result

    async def repo_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._emit("deerflow_repo_scan_started", payload)
        result = await self._runtime.repo_scan(payload)
        await self._emit("deerflow_repo_scan_completed", {**payload, **result})
        return result

    async def daily_evolution_brief(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._emit("deerflow_daily_brief_started", payload)
        result = await self._runtime.daily_evolution_brief(payload)
        await self._emit("deerflow_daily_brief_completed", {**payload, **result})
        return result

    async def handle_capability(self, capability: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        if capability == "evolution_research_cycle":
            return await self.evolution_research_cycle(payload)
        if capability == "paper_scan":
            return await self.paper_scan(payload)
        if capability == "repo_scan":
            return await self.repo_scan(payload)
        if capability == "daily_evolution_brief":
            return await self.daily_evolution_brief(payload)
        if capability == "health_check":
            return await self.health_check()
        return {"ok": False, "error": f"unknown capability '{capability}'"}

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await db.emit_event(
                event_type,
                {
                    "sender": self.name,
                    **payload,
                },
            )
        except Exception as exc:
            logger.debug("event emit failed for %s: %s", event_type, exc)


async def main() -> None:
    daemon = DeerFlowResearchDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))
    try:
        await daemon.start()
    except Exception as exc:
        logger.exception("DeerFlow research daemon crashed: %s", exc)
        raise


async def main_with_a2a() -> None:
    import uvicorn

    from .a2a_server import create_deerflow_research_a2a

    daemon = DeerFlowResearchDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))

    a2a_app = create_deerflow_research_a2a(daemon)
    a2a_port = int(os.environ.get("DEERFLOW_RESEARCH_A2A_PORT", "9011"))
    config = uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("DeerFlow research A2A server starting on :%d", a2a_port)
    await asyncio.gather(daemon.start(), server.serve())


if __name__ == "__main__":
    if os.environ.get("DEERFLOW_RESEARCH_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
