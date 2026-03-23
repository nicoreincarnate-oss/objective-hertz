"""Vassal Supervisor — start, stop, restart, and monitor daemon processes.

Replaces launchd/Makefile management. OpenJarvis starts daemon processes,
watches them, restarts on crash, and reports status.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass
class VassalProcess:
    """Configuration + runtime state for a vassal process."""

    name: str
    command: List[str]  # e.g. ["python", "-m", "titan.daemon"]
    cwd: str = ""  # working directory
    env: Dict[str, str] = field(default_factory=dict)
    a2a_port: int = 0
    restart_on_crash: bool = True
    max_restart_attempts: int = 5
    restart_backoff_seconds: float = 5.0

    # Runtime state
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    pid: Optional[int] = None
    started_at: float = 0.0
    restart_count: int = 0
    last_crash: float = 0.0
    status: str = "stopped"  # stopped, starting, running, crashed, disabled


class VassalSupervisor:
    """Manages vassal daemon lifecycle.

    Parameters
    ----------
    bus:
        OpenJarvis EventBus for publishing lifecycle events.
    project_dir:
        Path to objective-hertz project (where daemon modules live).
    """

    def __init__(self, bus: EventBus, project_dir: str = "") -> None:
        self._bus = bus
        self._project_dir = project_dir
        self._vassals: Dict[str, VassalProcess] = {}
        self._monitor_task: Optional[asyncio.Task] = None

    # ── Registration ──────────────────────────────────────────────────

    def register(self, vassal: VassalProcess) -> None:
        """Register a vassal process configuration."""
        self._vassals[vassal.name] = vassal
        logger.info("Registered vassal process: %s (cmd=%s)", vassal.name, vassal.command)

    def register_defaults(self, project_dir: str = "") -> None:
        """Register the default Perseus vassals (Titan, Hermes, ClawdBot)."""
        cwd = project_dir or self._project_dir
        if not cwd:
            logger.warning("No project_dir set — cannot register default vassals")
            return

        defaults = [
            VassalProcess(
                name="titan",
                command=["python", "-m", "titan.daemon"],
                cwd=cwd,
                a2a_port=9001,
                env={"TITAN_A2A": "1", "TITAN_A2A_PORT": "9001"},
            ),
            VassalProcess(
                name="hermes",
                command=["python", "-m", "hermes.daemon"],
                cwd=cwd,
                a2a_port=9002,
                env={"HERMES_A2A": "1", "HERMES_A2A_PORT": "9002"},
            ),
            VassalProcess(
                name="clawdbot",
                command=["python", "-m", "clawdbot.daemon"],
                cwd=cwd,
                a2a_port=9003,
                env={"CLAWDBOT_A2A": "1", "CLAWDBOT_A2A_PORT": "9003"},
            ),
        ]
        for v in defaults:
            self.register(v)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start_all(self) -> Dict[str, bool]:
        """Start all registered vassals. Returns name → success."""
        results = {}
        for name in self._vassals:
            results[name] = await self.start(name)

        # Start monitor loop
        if not self._monitor_task or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

        return results

    async def start(self, name: str) -> bool:
        """Start a single vassal process."""
        vassal = self._vassals.get(name)
        if not vassal:
            logger.error("Unknown vassal: %s", name)
            return False

        if vassal.process and vassal.process.poll() is None:
            logger.info("Vassal %s already running (pid=%d)", name, vassal.pid)
            return True

        try:
            env = {**os.environ, **vassal.env}
            cwd = vassal.cwd or self._project_dir or None

            vassal.process = subprocess.Popen(
                vassal.command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            vassal.pid = vassal.process.pid
            vassal.started_at = time.time()
            vassal.status = "running"

            logger.info("Started vassal %s (pid=%d, port=%d)", name, vassal.pid, vassal.a2a_port)
            self._bus.publish(EventType.CUSTOM, {
                "sub_type": "vassal_started",
                "name": name,
                "pid": vassal.pid,
                "port": vassal.a2a_port,
            })
            return True

        except Exception as exc:
            logger.error("Failed to start vassal %s: %s", name, exc)
            vassal.status = "crashed"
            return False

    async def stop(self, name: str, timeout: float = 10.0) -> bool:
        """Gracefully stop a vassal process."""
        vassal = self._vassals.get(name)
        if not vassal or not vassal.process:
            return True

        if vassal.process.poll() is not None:
            vassal.status = "stopped"
            vassal.process = None
            return True

        # Send SIGTERM first
        try:
            vassal.process.send_signal(signal.SIGTERM)
            vassal.status = "stopping"
        except (ProcessLookupError, OSError):
            vassal.status = "stopped"
            vassal.process = None
            return True

        # Wait for graceful shutdown
        start = time.time()
        while time.time() - start < timeout:
            if vassal.process.poll() is not None:
                vassal.status = "stopped"
                vassal.process = None
                logger.info("Vassal %s stopped gracefully", name)
                return True
            await asyncio.sleep(0.5)

        # Force kill
        try:
            vassal.process.kill()
            vassal.process.wait(timeout=5)
        except Exception:
            pass
        vassal.status = "stopped"
        vassal.process = None
        logger.warning("Vassal %s force-killed", name)
        return True

    async def stop_all(self) -> None:
        """Stop all vassals."""
        if self._monitor_task:
            self._monitor_task.cancel()
        for name in list(self._vassals.keys()):
            await self.stop(name)

    async def restart(self, name: str) -> bool:
        """Restart a vassal."""
        await self.stop(name)
        await asyncio.sleep(1)
        return await self.start(name)

    # ── Monitor ───────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Monitor vassal processes and restart crashed ones."""
        while True:
            try:
                await asyncio.sleep(10)
                for name, vassal in self._vassals.items():
                    if vassal.status == "disabled":
                        continue
                    if vassal.process and vassal.process.poll() is not None:
                        # Process died
                        exit_code = vassal.process.returncode
                        vassal.status = "crashed"
                        vassal.last_crash = time.time()
                        vassal.process = None
                        logger.warning(
                            "Vassal %s crashed (exit=%d, restarts=%d/%d)",
                            name, exit_code, vassal.restart_count, vassal.max_restart_attempts,
                        )
                        self._bus.publish(EventType.CUSTOM, {
                            "sub_type": "vassal_crashed",
                            "name": name,
                            "exit_code": exit_code,
                            "restart_count": vassal.restart_count,
                        })

                        if vassal.restart_on_crash and vassal.restart_count < vassal.max_restart_attempts:
                            backoff = vassal.restart_backoff_seconds * (vassal.restart_count + 1)
                            logger.info("Restarting %s in %.0fs...", name, backoff)
                            await asyncio.sleep(backoff)
                            vassal.restart_count += 1
                            await self.start(name)
                        else:
                            vassal.status = "disabled"
                            logger.error(
                                "Vassal %s exceeded max restarts (%d) — disabled",
                                name, vassal.max_restart_attempts,
                            )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Monitor loop error: %s", exc)

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Dict[str, Any]]:
        """Return status of all vassal processes."""
        result = {}
        for name, v in self._vassals.items():
            alive = v.process is not None and v.process.poll() is None
            uptime = time.time() - v.started_at if alive and v.started_at else 0
            result[name] = {
                "status": "running" if alive else v.status,
                "pid": v.pid if alive else None,
                "port": v.a2a_port,
                "uptime_seconds": int(uptime),
                "restart_count": v.restart_count,
                "last_crash": v.last_crash,
            }
        return result

    def is_running(self, name: str) -> bool:
        """Check if a vassal is running."""
        v = self._vassals.get(name)
        return v is not None and v.process is not None and v.process.poll() is None


__all__ = ["VassalProcess", "VassalSupervisor"]
