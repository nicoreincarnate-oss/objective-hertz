"""
Dynamic capability-based task routing via A2A agent discovery.

Replaces the hardcoded TASK_ROUTING dict in task_routing.py with
runtime discovery of agent capabilities via /.well-known/agent.json.
Falls back to static routing when A2A discovery is unavailable.
"""

import logging
import time
from typing import Any

import httpx

from shared.task_routing import TASK_ROUTING

logger = logging.getLogger("perseus.capability_router")


class CapabilityRouter:
    """Dynamic capability-based task routing via A2A discovery."""

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

    async def route(self, task_type: str) -> str | None:
        """Find the best agent for a task type. Returns agent name or None."""
        # Try dynamic routing first
        if not self._discovery_attempted or (time.time() - self._last_discovery) > self._cache_ttl:
            await self.discover_all()

        agent = self._capability_map.get(task_type)
        if agent:
            return agent

        # Fall back to static routing
        return TASK_ROUTING.get(task_type)

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
