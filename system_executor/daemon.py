"""System executor daemon.

Provides a small but real execution surface for desktop control, shell
execution, system actions, auth checkpoints, screen context, and health.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from typing import Any

from openjarvis.vassals.registry import heartbeat
from shared import db
from shared.agent_base import AgentBase
from shared.logging_config import setup_logging

from .adapters import CheckpointRecord, SystemExecutorRuntime

logger = setup_logging("system_executor")


class SystemExecutorDaemon(AgentBase):
    name = "system_executor"
    description = "Desktop control, shell execution, system actions, auth checkpoints, and screen context."

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._started_at = 0.0
        self._idle_interval = 30
        self._runtime = SystemExecutorRuntime()
        self._checkpoints: dict[str, CheckpointRecord] = {}

    async def start(self) -> None:
        logger.info("System executor starting...")
        try:
            await db.init_pool()
        except Exception as exc:
            logger.debug("System executor DB init skipped: %s", exc)
        await self.register()
        self._stopped.clear()
        self._running = True
        self._started_at = time.time()
        backend_status = await self._runtime.status("health_check")
        logger.info(
            "System executor live with preferred_backend=%s selected_backend=%s fallback_backend=%s order=%s",
            backend_status["preferred_backend"],
            backend_status["selected_backend"],
            backend_status["fallback_backend"],
            ",".join(backend_status["backend_order"]),
        )

        try:
            while self._running:
                self.begin_work("loop:heartbeat")
                try:
                    await heartbeat(self.name)
                except Exception as exc:
                    logger.debug("System executor heartbeat failed: %s", exc)
                finally:
                    self.finish_work("loop:heartbeat")
                await asyncio.sleep(self._idle_interval)
        finally:
            await self.finalize_shutdown()

    async def stop(self) -> None:
        logger.info("System executor shutdown requested...")
        self.request_shutdown()
        drained = await self.wait_for_work_drain()
        if not drained:
            logger.warning("System executor shutdown with pending work")
        self._running = False
        await self.wait_until_stopped()

    async def health_check(self) -> dict[str, Any]:
        backend_status = await self._runtime.status("health_check")
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "uptime_seconds": round(time.time() - self._started_at, 2) if self._started_at else 0.0,
            "active_backend": backend_status["selected_backend"],
            "preferred_backend": backend_status["preferred_backend"],
            "fallback_backend": backend_status["fallback_backend"],
            "backend_order": backend_status["backend_order"],
            "backends": backend_status["adapters"],
            "backend_status": backend_status,
            "open_checkpoints": sum(1 for checkpoint in self._checkpoints.values() if checkpoint.status == "pending"),
            "checkpoint_count": len(self._checkpoints),
        }

    async def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        payload.setdefault("action", payload.get("action", ""))
        return await self._runtime.desktop_control(payload)

    async def desktop_exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._runtime.desktop_exec(dict(payload or {}))

    async def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._runtime.system_action(dict(payload or {}))

    async def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._runtime.screen_context(dict(payload or {}))

    async def auth_checkpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        operation = str(payload.get("operation", "request") or "request").strip().lower()
        checkpoint_id = str(payload.get("checkpoint_id", "") or "").strip()

        if operation in {"request", "create", "checkpoint"}:
            checkpoint_id = checkpoint_id or uuid.uuid4().hex[:12]
            record = CheckpointRecord(
                checkpoint_id=checkpoint_id,
                status="pending",
                purpose=str(payload.get("purpose", "") or ""),
                requested_by=str(payload.get("requested_by", payload.get("requester", "")) or ""),
            )
            self._checkpoints[checkpoint_id] = record
            try:
                await db.emit_event(
                    "system_auth_checkpoint_requested",
                    {
                        "checkpoint_id": checkpoint_id,
                        "purpose": record.purpose,
                        "requested_by": record.requested_by,
                    },
                )
            except Exception as exc:
                logger.debug("auth_checkpoint event emit failed: %s", exc)
            return {
                "ok": True,
                "backend": "local-checkpoint-store",
                "checkpoint_id": checkpoint_id,
                "status": "pending",
                "purpose": record.purpose,
            }

        if operation in {"resolve", "approve", "deny"}:
            if not checkpoint_id:
                return {"ok": False, "error": "checkpoint_id is required"}
            record = self._checkpoints.get(checkpoint_id)
            if record is None:
                return {"ok": False, "error": f"checkpoint '{checkpoint_id}' not found"}
            resolution = {
                "decision": "approved" if operation == "approve" else "denied" if operation == "deny" else str(payload.get("decision", "approved")),
                "resolved_by": str(payload.get("resolved_by", payload.get("operator", "")) or ""),
                "notes": str(payload.get("notes", "") or ""),
            }
            record.status = resolution["decision"]
            record.resolution = resolution
            record.resolved_at = time.time()
            try:
                await db.emit_event(
                    "system_auth_checkpoint_resolved",
                    {
                        "checkpoint_id": checkpoint_id,
                        **resolution,
                    },
                )
            except Exception as exc:
                logger.debug("auth_checkpoint resolve emit failed: %s", exc)
            return {
                "ok": True,
                "backend": "local-checkpoint-store",
                "checkpoint_id": checkpoint_id,
                "status": record.status,
                "resolved_at": record.resolved_at,
            }

        if operation in {"get", "status"}:
            if not checkpoint_id:
                return {"ok": True, "checkpoints": [self._checkpoint_dict(item) for item in self._checkpoints.values()]}
            record = self._checkpoints.get(checkpoint_id)
            return self._checkpoint_dict(record) if record else {"ok": False, "error": f"checkpoint '{checkpoint_id}' not found"}

        return {"ok": False, "error": f"unknown auth_checkpoint operation '{operation}'"}

    def _checkpoint_dict(self, record: CheckpointRecord | None) -> dict[str, Any]:
        if record is None:
            return {}
        return {
            "checkpoint_id": record.checkpoint_id,
            "status": record.status,
            "purpose": record.purpose,
            "requested_by": record.requested_by,
            "created_at": record.created_at,
            "resolved_at": record.resolved_at,
            "resolution": record.resolution,
        }

    async def handle_capability(self, capability: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        if capability == "desktop_control":
            return await self.desktop_control(payload)
        if capability == "desktop_exec":
            return await self.desktop_exec(payload)
        if capability == "system_action":
            return await self.system_action(payload)
        if capability == "auth_checkpoint":
            return await self.auth_checkpoint(payload)
        if capability == "screen_context":
            return await self.screen_context(payload)
        if capability == "health_check":
            return await self.health_check()
        return {"ok": False, "error": f"unknown capability '{capability}'"}


async def main() -> None:
    executor = SystemExecutorDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(executor.stop()))
    try:
        await executor.start()
    except Exception as exc:
        logger.exception("System executor crashed: %s", exc)
        raise


async def main_with_a2a() -> None:
    import uvicorn

    from .a2a_server import create_system_executor_a2a

    executor = SystemExecutorDaemon()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(executor.stop()))

    a2a_app = create_system_executor_a2a(executor)
    a2a_port = int(os.environ.get("SYSTEM_EXECUTOR_A2A_PORT", "9010"))
    config = uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("System executor A2A server starting on :%d", a2a_port)
    await asyncio.gather(executor.start(), server.serve())


if __name__ == "__main__":
    if os.environ.get("SYSTEM_EXECUTOR_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
