"""EventRelay — bridges OpenJarvis EventBus with vassal A2A events.

Bidirectional:
1. Local EventBus events → relayed to vassals via A2A
2. Vassal events → polled via A2A → published on local EventBus

This gives OpenJarvis full observability across all nodes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)


class EventRelay:
    """Bidirectional event relay between OpenJarvis and A2A vassals.

    Parameters
    ----------
    bus:
        Local OpenJarvis EventBus.
    vassal_discovery:
        VassalDiscovery instance for accessing vassal clients.
    forward_types:
        EventTypes to forward FROM OpenJarvis TO vassals.
    poll_interval:
        Seconds between polling vassals for their events.
    """

    def __init__(
        self,
        bus: EventBus,
        vassal_discovery: Any,
        forward_types: list[str] | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self._bus = bus
        self._vassals = vassal_discovery
        self._poll_interval = poll_interval
        self._running = False
        self._seen_events: set[str] = set()  # dedup by event hash
        self._max_seen = 10000

        # Default: forward security alerts, budget warnings, and strategy overrides
        self._forward_types = set(forward_types or [
            "SECURITY_ALERT",
            "SECURITY_BLOCK",
            "budget_exceeded",
            "budget_warning",
            "strategy_override",
            "perseus_alert",
        ])

        # Subscribe to local events we want to forward
        self._bus.subscribe(EventType.CUSTOM, self._on_local_event)

    # ── Outbound: local → vassals ─────────────────────────────────────

    async def _on_local_event(self, event: Any) -> None:
        """Forward select local events to all vassals."""
        payload = event if isinstance(event, dict) else getattr(event, "payload", {})
        sub_type = payload.get("sub_type", "")

        if sub_type not in self._forward_types:
            return

        for _name, vassal in self._vassals.vassals.items():
            if not vassal.healthy:
                continue
            try:
                vassal.client.send_task(json.dumps({
                    "capability": "event_relay",
                    "params": {
                        "type": sub_type,
                        "payload": payload,
                        "source": "openjarvis",
                    },
                }))
            except Exception:
                pass  # non-critical

    # ── Inbound: vassals → local ──────────────────────────────────────

    async def start_polling(self) -> None:
        """Start polling vassals for their events."""
        self._running = True
        logger.info("EventRelay polling started (interval=%.0fs)", self._poll_interval)
        while self._running:
            await self._poll_all()
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _poll_all(self) -> None:
        """Poll all healthy vassals for recent events."""
        for name, vassal in self._vassals.vassals.items():
            if not vassal.healthy:
                continue
            try:
                raw = vassal.client.send_task(json.dumps({
                    "capability": "events_recent",
                    "params": {"limit": 10},
                }))
                events = json.loads(raw.output_text) if isinstance(raw.output_text, str) else raw.output_text
                if isinstance(events, list):
                    for event in events:
                        self._ingest_remote_event(name, event)
                elif isinstance(events, dict) and "error" not in events:
                    # Some vassals might not have events_recent
                    pass
            except Exception:
                pass  # vassal might not support events_recent yet

    def _ingest_remote_event(self, source: str, event: dict[str, Any]) -> None:
        """Publish a remote event on the local EventBus (deduped)."""
        event_key = f"{source}:{event.get('event_type', '')}:{event.get('created_at', '')}"
        if event_key in self._seen_events:
            return
        self._seen_events.add(event_key)

        # Cap seen set
        if len(self._seen_events) > self._max_seen:
            # Remove oldest half
            to_remove = list(self._seen_events)[:self._max_seen // 2]
            for k in to_remove:
                self._seen_events.discard(k)

        self._bus.publish(EventType.CUSTOM, {
            "sub_type": "vassal_event",
            "source": source,
            "event_type": event.get("event_type", ""),
            "payload": event.get("payload", {}),
            "created_at": event.get("created_at", ""),
        })

    # ── Manual relay ──────────────────────────────────────────────────

    def relay_to_vassal(self, vassal_name: str, event_type: str, payload: dict[str, Any]) -> None:
        """Manually relay an event to a specific vassal."""
        vassal = self._vassals.get(vassal_name)
        if not vassal or not vassal.healthy:
            return
        try:
            vassal.client.send_task(json.dumps({
                "capability": "event_relay",
                "params": {"type": event_type, "payload": payload, "source": "openjarvis"},
            }))
        except Exception as exc:
            logger.debug("Failed to relay event to %s: %s", vassal_name, exc)

    def relay_to_all(self, event_type: str, payload: dict[str, Any]) -> None:
        """Relay an event to all healthy vassals."""
        for name in self._vassals.vassals:
            self.relay_to_vassal(name, event_type, payload)


__all__ = ["EventRelay"]
