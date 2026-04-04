"""
Dynamic capability-based task routing via A2A agent discovery.

Replaces the hardcoded TASK_ROUTING dict in task_routing.py with
runtime discovery of agent capabilities via /.well-known/agent.json.
Falls back to static routing when A2A discovery is unavailable.

Now also queries the UnifiedToolRegistry for local tool matches
before falling back to remote A2A agents.
"""

import logging
import os
import time
from typing import Any

import httpx

from shared.task_routing import TASK_ROUTING

logger = logging.getLogger("perseus.capability_router")

# Feature flag — set to "false" to disable ToolRegistry lookup
USE_TOOL_REGISTRY = os.environ.get("USE_TOOL_REGISTRY", "true").lower() != "false"


class CapabilityRouter:
    """Dynamic capability-based task routing via A2A discovery.

    Routing priority (when USE_TOOL_REGISTRY is True):
    1. Local ToolRegistry — returns tool name for direct execution
    2. Dynamic A2A agent discovery via /.well-known/agent.json
    3. Static TASK_ROUTING dict fallback
    """

    def __init__(
        self,
        agent_urls: dict[str, str] | None = None,
        cache_ttl: int = 300,
    ):
        if agent_urls is None:
            from shared.oj_bridge import AGENT_URLS
            agent_urls = AGENT_URLS
        self._agent_urls = agent_urls
        self._cache_ttl = cache_ttl
        # capability -> agent_name
        self._capability_map: dict[str, str] = {}
        self._agent_cards: dict[str, dict[str, Any]] = {}
        self._last_discovery: float = 0.0
        self._discovery_attempted: bool = False

    async def discover_all(self) -> dict[str, dict[str, Any]]:
        """Discover all agent capabilities via /.well-known/agent.json."""
        now = time.time()
        if self._discovery_attempted and (now - self._last_discovery) < self._cache_ttl:
            return self._agent_cards

        self._discovery_attempted = True
        self._last_discovery = now

        for agent_name, url in self._agent_urls.items():
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{url}/.well-known/agent.json")
                    if resp.status_code == 200:
                        card = resp.json()
                        self._agent_cards[agent_name] = card

                        # Index capabilities
                        for cap in card.get("capabilities", []):
                            cap_name = cap if isinstance(cap, str) else cap.get("name", "")
                            if cap_name:
                                self._capability_map[cap_name] = agent_name

                        # Also index skills
                        for skill in card.get("skills", []):
                            skill_name = skill if isinstance(skill, str) else skill.get("name", "")
                            if skill_name:
                                self._capability_map[skill_name] = agent_name
            except Exception as e:
                logger.debug(f"Discovery failed for {agent_name} at {url}: {e}")

        logger.info(
            f"Capability discovery: {len(self._capability_map)} capabilities "
            f"from {len(self._agent_cards)} agents"
        )
        return self._agent_cards

    async def route_to_tool(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Query the ToolRegistry for a tool and optionally execute it.

        Parameters
        ----------
        tool_name : str
            Exact tool name or search query to match.
        params : dict | None
            If provided, execute the tool with these params and return the result.
            If None, return the ToolSpec metadata without executing.

        Returns
        -------
        dict | None
            Tool metadata dict (if params is None), execution result dict
            (if params provided), or None if tool not found / registry disabled.
        """
        if not USE_TOOL_REGISTRY:
            return None

        try:
            from shared.tool_registry import get_registry

            registry = get_registry()

            # Try exact name match first
            spec = registry.find(tool_name)
            if spec is None:
                # Try fuzzy search
                results = registry.search(query=tool_name)
                if results:
                    spec = results[0]

            if spec is None:
                return None

            if params is None:
                return {
                    "source": "tool_registry",
                    "tool_name": spec.name,
                    "description": spec.description,
                    "tool_type": spec.tool_type.value,
                    "available": spec.env_satisfied(),
                }

            # Execute the tool
            result = await registry.execute(spec.name, params)
            return {
                "source": "tool_registry",
                "tool_name": spec.name,
                "success": result.success,
                "content": result.content,
                "latency_seconds": getattr(result, "latency_seconds", 0.0),
            }

        except Exception as exc:
            logger.debug("ToolRegistry lookup failed for '%s': %s", tool_name, exc)
            return None

    async def route(self, task_type: str) -> str | None:
        """Find the best agent for a task type. Returns agent name or None.

        Routing priority:
        1. ToolRegistry (local tools) — returns "tool:<name>" prefix
        2. Dynamic A2A agent discovery
        3. Static TASK_ROUTING fallback
        """
        # Check ToolRegistry first (if enabled)
        if USE_TOOL_REGISTRY:
            tool_match = await self._check_tool_registry(task_type)
            if tool_match:
                return tool_match

        # Try dynamic routing
        if not self._discovery_attempted or (time.time() - self._last_discovery) > self._cache_ttl:
            await self.discover_all()

        agent = self._capability_map.get(task_type)
        if agent:
            return agent

        # Fall back to static routing
        return TASK_ROUTING.get(task_type)

    async def _check_tool_registry(self, task_type: str) -> str | None:
        """Check if a local tool matches the task type.

        Returns "tool:<tool_name>" if found, None otherwise.
        """
        try:
            from shared.tool_registry import get_registry

            registry = get_registry()

            # Exact name match
            spec = registry.find(task_type)
            if spec and spec.env_satisfied():
                return f"tool:{spec.name}"

            # Search by tag or name substring
            results = registry.search(query=task_type)
            for spec in results:
                if spec.env_satisfied():
                    return f"tool:{spec.name}"

        except Exception as exc:
            logger.debug("ToolRegistry check failed for '%s': %s", task_type, exc)

        return None

    async def route_by_requirements(
        self, requirements: list[str]
    ) -> str | None:
        """Match task requirements against agent capability sets."""
        if not self._discovery_attempted:
            await self.discover_all()

        # Score each agent by how many requirements they satisfy
        scores: dict[str, int] = {}
        for agent_name, card in self._agent_cards.items():
            agent_caps = set()
            for cap in card.get("capabilities", []):
                cap_name = cap if isinstance(cap, str) else cap.get("name", "")
                if cap_name:
                    agent_caps.add(cap_name)
            for skill in card.get("skills", []):
                skill_name = skill if isinstance(skill, str) else skill.get("name", "")
                if skill_name:
                    agent_caps.add(skill_name)

            score = sum(1 for req in requirements if req in agent_caps)
            if score > 0:
                scores[agent_name] = score

        if not scores:
            return None

        # Return agent with highest score
        return max(scores, key=lambda k: scores[k])

    def invalidate_cache(self, agent_name: str = "") -> None:
        """Force re-discovery on next route call."""
        if agent_name:
            self._agent_cards.pop(agent_name, None)
            # Remove capabilities from this agent
            self._capability_map = {
                k: v for k, v in self._capability_map.items() if v != agent_name
            }
        else:
            self._agent_cards.clear()
            self._capability_map.clear()
        self._discovery_attempted = False


# Module-level singleton
_router: CapabilityRouter | None = None


def get_capability_router() -> CapabilityRouter:
    """Get or create the singleton CapabilityRouter."""
    global _router
    if _router is None:
        _router = CapabilityRouter()
    return _router
