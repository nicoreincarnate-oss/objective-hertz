"""Vassal discovery — find A2A daemons and register their capabilities as tools.

On startup, OpenJarvis scans configured vassal endpoints, fetches each one's
AgentCard, and dynamically registers every capability as an OpenJarvis tool.
This means any OpenJarvis agent can call ``titan.pipeline_status`` or
``hermes.message_send`` like any other tool — the LLM doesn't know or care
that the tool is backed by a remote daemon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openjarvis.a2a.client import A2AClient
from openjarvis.a2a.protocol import AgentCard
from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass
class VassalInfo:
    """Runtime state for a discovered vassal."""

    card: AgentCard
    client: A2AClient
    url: str
    healthy: bool = True
    last_error: str = ""
    capabilities: list[str] = field(default_factory=list)


class VassalDiscovery:
    """Discovers A2A vassals and registers their capabilities as OpenJarvis tools.

    Parameters
    ----------
    bus:
        OpenJarvis EventBus for publishing discovery events.
    tool_registry:
        Optional ToolRegistry — if provided, each vassal capability is
        registered as a callable tool.
    config:
        Dict of vassal configs: ``{"titan": {"url": "http://...", "token": "..."}, ...}``
    """

    def __init__(
        self,
        bus: EventBus,
        tool_registry: Any | None = None,
        config: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._bus = bus
        self._tool_registry = tool_registry
        self._config = config or {}
        self._vassals: dict[str, VassalInfo] = {}

    @property
    def vassals(self) -> dict[str, VassalInfo]:
        return self._vassals

    def get(self, name: str) -> VassalInfo | None:
        return self._vassals.get(name)

    # ── Discovery ─────────────────────────────────────────────────────

    def discover_all(self) -> dict[str, VassalInfo]:
        """Discover all configured vassals. Returns map of name → VassalInfo."""
        for name, cfg in self._config.items():
            url = cfg.get("url", "")
            if not url:
                logger.warning("Vassal %s has no URL configured, skipping", name)
                continue
            self.discover_one(name, url)
        return self._vassals

    def discover_one(self, name: str, url: str) -> VassalInfo | None:
        """Discover a single vassal by URL."""
        client = A2AClient(url, timeout=10.0)
        try:
            card = client.discover()
            vassal = VassalInfo(
                card=card,
                client=client,
                url=url,
                healthy=True,
                capabilities=list(card.capabilities),
            )
            self._vassals[card.name or name] = vassal

            # Register each capability as an OpenJarvis tool
            if self._tool_registry:
                self._register_tools(vassal)

            self._bus.publish(EventType.CUSTOM, {
                "sub_type": "vassal_discovered",
                "name": card.name or name,
                "url": url,
                "capabilities": len(card.capabilities),
                "description": card.description,
            })
            logger.info(
                "Discovered vassal '%s' at %s with %d capabilities",
                card.name or name, url, len(card.capabilities),
            )
            return vassal

        except Exception as exc:
            logger.warning("Failed to discover vassal '%s' at %s: %s", name, url, exc)
            self._bus.publish(EventType.CUSTOM, {
                "sub_type": "vassal_unreachable",
                "name": name,
                "url": url,
                "error": str(exc),
            })
            # Store as unhealthy so supervisor can try to revive
            self._vassals[name] = VassalInfo(
                card=AgentCard(name=name, url=url),
                client=client,
                url=url,
                healthy=False,
                last_error=str(exc),
            )
            return None

    # ── Tool registration ─────────────────────────────────────────────

    def _register_tools(self, vassal: VassalInfo) -> None:
        """Register each vassal capability as a dynamic OpenJarvis tool."""
        vassal_name = vassal.card.name

        for cap_name in vassal.capabilities:
            tool_name = f"{vassal_name}.{cap_name}"
            self._register_one_tool(tool_name, cap_name, vassal)

        # Also register a catch-all "ask" tool for natural language
        ask_tool_name = f"{vassal_name}.ask"
        self._register_ask_tool(ask_tool_name, vassal)

    def _register_one_tool(self, tool_name: str, cap_name: str, vassal: VassalInfo) -> None:
        """Register a single capability as a tool."""
        description = (
            f"[{vassal.card.name}] {cap_name} — "
            f"Call {vassal.card.name}'s {cap_name} capability via A2A. "
            f"Send JSON params or natural language."
        )

        def make_executor(v: VassalInfo, c: str):
            def execute(**params: Any) -> str:
                """Execute vassal capability via A2A."""
                input_text = json.dumps({"capability": c, "params": params})
                try:
                    task = v.client.send_task(input_text)
                    v.healthy = True
                    v.last_error = ""
                    return task.output_text
                except Exception as exc:
                    v.healthy = False
                    v.last_error = str(exc)
                    return json.dumps({"error": str(exc), "vassal": v.card.name})
            return execute

        if hasattr(self._tool_registry, "register_dynamic"):
            self._tool_registry.register_dynamic(
                name=tool_name,
                description=description,
                execute_fn=make_executor(vassal, cap_name),
            )
        logger.debug("Registered tool: %s", tool_name)

    def _register_ask_tool(self, tool_name: str, vassal: VassalInfo) -> None:
        """Register a natural-language 'ask' tool for the vassal."""
        description = (
            f"[{vassal.card.name}] Ask {vassal.card.name} anything in natural language. "
            f"{vassal.card.description}"
        )

        def make_ask(v: VassalInfo):
            def execute(question: str = "", **params: Any) -> str:
                text = question or json.dumps(params)
                try:
                    task = v.client.send_task(text)
                    v.healthy = True
                    return task.output_text
                except Exception as exc:
                    v.healthy = False
                    v.last_error = str(exc)
                    return json.dumps({"error": str(exc), "vassal": v.card.name})
            return execute

        if hasattr(self._tool_registry, "register_dynamic"):
            self._tool_registry.register_dynamic(
                name=tool_name,
                description=description,
                execute_fn=make_ask(vassal),
            )

    # ── Health ────────────────────────────────────────────────────────

    def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Check health of all vassals. Returns name → status dict."""
        results = {}
        for name, vassal in self._vassals.items():
            try:
                task = vassal.client.send_task(
                    json.dumps({"capability": "health_check", "params": {}})
                )
                vassal.healthy = True
                vassal.last_error = ""
                results[name] = {
                    "healthy": True,
                    "url": vassal.url,
                    "capabilities": len(vassal.capabilities),
                    "response": task.output_text[:200],
                }
            except Exception as exc:
                vassal.healthy = False
                vassal.last_error = str(exc)
                results[name] = {
                    "healthy": False,
                    "url": vassal.url,
                    "error": str(exc),
                }
        return results

    def rediscover_unhealthy(self) -> list[str]:
        """Try to rediscover vassals that were previously unreachable."""
        recovered = []
        for name, vassal in self._vassals.items():
            if vassal.healthy:
                continue
            result = self.discover_one(name, vassal.url)
            if result and result.healthy:
                recovered.append(name)
        return recovered

    # ── Convenience ───────────────────────────────────────────────────

    def call(self, vassal_name: str, capability: str, **params: Any) -> str:
        """Convenience: call a vassal capability directly.

        Returns JSON string of the result.
        """
        vassal = self._vassals.get(vassal_name)
        if not vassal:
            return json.dumps({"error": f"Unknown vassal: {vassal_name}"})
        if not vassal.healthy:
            return json.dumps({"error": f"Vassal {vassal_name} is unhealthy: {vassal.last_error}"})

        input_text = json.dumps({"capability": capability, "params": params})
        try:
            task = vassal.client.send_task(input_text)
            vassal.healthy = True
            return task.output_text
        except Exception as exc:
            vassal.healthy = False
            vassal.last_error = str(exc)
            return json.dumps({"error": str(exc)})

    def list_all_tools(self) -> list[str]:
        """List all registered vassal tools."""
        tools = []
        for name, vassal in self._vassals.items():
            for cap in vassal.capabilities:
                tools.append(f"{name}.{cap}")
            tools.append(f"{name}.ask")
        return tools

    def summary(self) -> dict[str, Any]:
        """Return a summary of all vassals for display."""
        return {
            name: {
                "url": v.url,
                "healthy": v.healthy,
                "capabilities": len(v.capabilities),
                "description": v.card.description[:100],
                "last_error": v.last_error[:100] if v.last_error else "",
            }
            for name, v in self._vassals.items()
        }


__all__ = ["VassalDiscovery", "VassalInfo"]
