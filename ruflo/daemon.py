"""Runnable Ruflo daemon with A2A surface."""

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

from .agent import dispatch

logger = setup_logging("ruflo")


class RufloDaemon(AgentBase):
    name = "ruflo"
    description = "Engineering daemon for code fixes, reviews, audits, and test generation."

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._started_at = 0.0
        self._idle_interval = int(os.environ.get("RUFLO_HEARTBEAT_SECONDS", "45"))

    async def start(self) -> None:
        logger.info("Ruflo daemon starting...")
        try:
            await db.init_pool()
        except Exception as exc:
            logger.debug("Ruflo DB init skipped: %s", exc)
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
                    logger.debug("Ruflo heartbeat failed: %s", exc)
                finally:
                    self.finish_work("loop:heartbeat")
                await asyncio.sleep(self._idle_interval)
        finally:
            await self.finalize_shutdown()

    async def stop(self) -> None:
        logger.info("Ruflo shutdown requested...")
        self.request_shutdown()
        await self.wait_for_work_drain()
        self._running = False
        await self.wait_until_stopped()

    async def health_check(self) -> dict[str, Any]:
        return {
            "agent": "ruflo",
            "status": "running" if self._running else "stopped",
            "uptime_seconds": round(time.time() - self._started_at, 2) if self._started_at else 0.0,
            "capabilities": [
                "code_fix",
                "code_review",
                "code_refactor",
                "security_scan",
                "dependency_audit",
                "implement_tool",
                "test_generate",
            ],
        }

    async def handle_capability(self, capability: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if capability == "health_check":
            return await self.health_check()
        task = {
            "task_type": capability,
            "payload": dict(payload or {}),
        }
        return await dispatch(task)


async def main() -> None:
    daemon = RufloDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))
    try:
        await daemon.start()
    except Exception as exc:
        logger.exception("Ruflo daemon crashed: %s", exc)
        raise


async def main_with_a2a() -> None:
    import uvicorn

    from .a2a_server import create_ruflo_a2a

    daemon = RufloDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))

    a2a_app = create_ruflo_a2a(daemon)
    a2a_port = int(os.environ.get("RUFLO_A2A_PORT", "9004"))
    config = uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("Ruflo A2A server starting on :%d", a2a_port)
    await asyncio.gather(daemon.start(), server.serve())


if __name__ == "__main__":
    if os.environ.get("RUFLO_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
