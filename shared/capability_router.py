"""
Dynamic capability-based task routing via A2A agent discovery.

Replaces the hardcoded TASK_ROUTING dict in task_routing.py with
runtime discovery of agent capabilities via /.well-known/agent.json.
Falls back to static routing when A2A discovery is unavailable.

Now also queries the UnifiedToolRegistry for local tool matches
before falling back to remote A2A agents.

Phase 31 additions:
- UGO (Utility-Guided Orchestration) utility scorer
- score_actions() evaluates utility before each tool/agent call
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
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
            except (httpx.HTTPError, OSError, ValueError, KeyError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
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

        except (ImportError, AttributeError, KeyError, ValueError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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

        except (ImportError, AttributeError, KeyError, ValueError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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


# ---------------------------------------------------------------------------
# Phase 31: UGO — Utility-Guided Orchestration
# ---------------------------------------------------------------------------

def _ugo_enabled() -> bool:
    return os.environ.get("UGO_UTILITY_ROUTING", "true").lower() in ("true", "1", "yes")


# Default lambda weights — tunable via bandit (Task 3c)
LAMBDA_COST = 0.3
LAMBDA_UNCERTAINTY = 0.5
LAMBDA_REDUNDANCY = 0.8

# Action types that UGO evaluates
UGO_ACTIONS = ("respond", "retrieve", "tool_call", "verify", "stop")


@dataclass
class UtilityScore:
    """Scored action candidate from UGO evaluation."""
    action: str             # respond | retrieve | tool_call | verify | stop
    gain: float             # estimated information gain
    step_cost: float        # normalized cost (0-1)
    uncertainty: float      # current uncertainty (0-1)
    redundancy: float       # overlap with prior actions (0-1)
    utility: float          # computed U(a|s_t)


def _estimate_gain(action: str, current_confidence: float) -> float:
    """Heuristic gain estimation per action type."""
    if action == "respond":
        return current_confidence  # high confidence = good time to respond
    if action == "retrieve":
        return 1.0 - current_confidence  # low confidence = high gain from retrieval
    if action == "tool_call":
        return 0.6  # fixed moderate — tools generally useful
    if action == "verify":
        return 0.3  # fixed modest — verification has diminishing returns
    return 0.0  # stop: no new information


def _estimate_cost(
    action: str,
    budget: object | None = None,
) -> float:
    """Normalized cost estimation per action type."""
    if action == "respond":
        return 0.05  # just the response tokens
    if action == "retrieve":
        return 0.15  # MAGMA query + embedding
    if action == "tool_call":
        if budget is not None and hasattr(budget, "token_budget_usd"):
            # Normalize tool cost against budget
            avg_tool_cost = 0.01  # default for external tools
            budget_usd = max(getattr(budget, "token_budget_usd", 1.0), 0.001)
            return min(avg_tool_cost / budget_usd, 1.0)
        return 0.20
    if action == "verify":
        return 0.10  # verification LLM call
    return 0.0  # stop: free


def _estimate_redundancy(
    action: str,
    action_history: list[str],
    tool_call_hashes: set[str],
) -> float:
    """Estimate redundancy of an action given history."""
    # Count how many times this action type appeared
    type_count = sum(1 for a in action_history if a == action)

    if type_count > 3:
        return 0.5  # partial redundancy after 3+ of same type

    # For tool_call, check if we have exact hash matches
    if action == "tool_call" and tool_call_hashes:
        # If there are any hashes, there's some redundancy risk
        return min(len(tool_call_hashes) * 0.1, 0.5)

    return 0.0


def score_actions(
    current_confidence: float,
    budget: object | None = None,
    action_history: list[str] | None = None,
    tool_call_hashes: set[str] | None = None,
    available_tools: list[str] | None = None,
    lambda_cost: float = LAMBDA_COST,
    lambda_uncertainty: float = LAMBDA_UNCERTAINTY,
    lambda_redundancy: float = LAMBDA_REDUNDANCY,
) -> list[UtilityScore]:
    """Score all available actions by utility. Returns sorted highest-first.

    Implements UGO (Utility-Guided Orchestration) from Phase 31.
    When UGO_UTILITY_ROUTING is disabled, returns a default ordering.

    Args:
        current_confidence: agent's confidence in its current answer (0-1)
        budget: UnifiedBudget instance for cost normalization
        action_history: list of previously taken action types
        tool_call_hashes: set of (tool_name:args) hashes already executed
        available_tools: list of available tool names (unused currently)
        lambda_cost: cost penalty weight
        lambda_uncertainty: uncertainty penalty weight
        lambda_redundancy: redundancy penalty weight

    Returns:
        List of UtilityScore sorted by utility (highest first)
    """
    if not _ugo_enabled():
        # Default ordering when UGO is off
        return [
            UtilityScore(a, 0.5, 0.0, 0.0, 0.0, 0.5)
            for a in UGO_ACTIONS
        ]

    hist = action_history or []
    hashes = tool_call_hashes or set()

    # Budget-regime bias: in LOW/CRITICAL, bias toward respond/stop
    regime_bias: dict[str, float] = {}
    if budget is not None and hasattr(budget, "budget_regime"):
        regime = getattr(budget, "budget_regime", "HIGH")
        if regime in ("LOW", "CRITICAL"):
            regime_bias = {"respond": 0.3, "stop": 0.2}
        elif regime == "MEDIUM":
            regime_bias = {"respond": 0.1}

    scores = []
    for action in UGO_ACTIONS:
        gain = _estimate_gain(action, current_confidence) + regime_bias.get(action, 0.0)
        cost = _estimate_cost(action, budget)
        uncertainty = 1.0 - current_confidence
        redundancy = _estimate_redundancy(action, hist, hashes)

        utility = (
            gain
            - lambda_cost * cost
            - lambda_uncertainty * uncertainty
            - lambda_redundancy * redundancy
        )
        scores.append(UtilityScore(action, gain, cost, uncertainty, redundancy, utility))

    return sorted(scores, key=lambda s: s.utility, reverse=True)
