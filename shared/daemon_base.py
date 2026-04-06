"""Cooperative shutdown mixin for vassal daemons.

Provides a standard interface for handling shutdown_request protocol
messages from the orchestrator. Daemons using this mixin can finish
their current operation before exiting.

Feature flag: ANATOMY_TASK_RESILIENCE
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CooperativeShutdownMixin:
    """Mixin providing cooperative shutdown for A2A-enabled daemons.

    Subclass must check ``self._shutdown_requested`` in its main loop
    and exit gracefully when True.

    Attributes:
        _shutdown_requested: Set to True when shutdown is requested.
        _current_task_description: Description of currently running task (or None).
    """

    _shutdown_requested: bool = False
    _current_task_description: str | None = None

    async def handle_shutdown_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle a protocol.shutdown_request from the orchestrator.

        Sets the shutdown flag and returns an acknowledgement with an
        estimated completion time.

        Args:
            payload: The shutdown_request payload (may contain ``reason``
                     and ``deadline_seconds``).

        Returns:
            A dict with ``acknowledged``, ``estimated_completion_seconds``,
            and ``current_task`` fields.
        """
        self._shutdown_requested = True
        est_seconds = 5.0  # Default: finish within 5 seconds

        reason = payload.get("reason", "unspecified")
        if self._current_task_description:
            est_seconds = 15.0  # Give more time if actively working
            logger.info(
                "Shutdown requested (reason=%s) while working on: %s",
                reason,
                self._current_task_description,
            )
        else:
            logger.info("Shutdown requested (reason=%s, idle)", reason)

        return {
            "acknowledged": True,
            "estimated_completion_seconds": est_seconds,
            "current_task": self._current_task_description,
        }

    @property
    def is_shutdown_requested(self) -> bool:
        """Check if a cooperative shutdown has been requested."""
        return self._shutdown_requested
