"""Tests for AgentCard auto-population from ToolRegistry.

Phase 5a T4: AgentCards should dynamically populate capabilities from
the UnifiedToolRegistry instead of relying solely on hardcoded lists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — lightweight ToolSpec / registry stand-ins for isolation
# ---------------------------------------------------------------------------

@dataclass
class _FakeToolSpec:
    name: str = ""
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    tool_type: Any = None  # will use a mock
    requires_env: list[str] = field(default_factory=list)

    def env_satisfied(self) -> bool:
        return True


class _FakeToolType:
    value = "native"


class _FakeRegistry:
    """Minimal stand-in for UnifiedToolRegistry singleton."""

    def __init__(self, specs: list[_FakeToolSpec] | None = None):
        self._specs = specs or []

    def available(self) -> list[_FakeToolSpec]:
        return self._specs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_specs(*tool_defs: tuple[str, str, str]) -> list[_FakeToolSpec]:
    """Create a list of FakeToolSpec from (name, description, category) tuples."""
    return [
        _FakeToolSpec(
            name=name,
            description=desc,
            category=cat,
            tool_type=_FakeToolType(),
        )
        for name, desc, cat in tool_defs
    ]


SAMPLE_SPECS = _make_specs(
    ("web_search", "Search the web", "search"),
    ("email_send", "Send outbound email", "outreach"),
    ("lead_score", "Score a lead", "pipeline"),
    ("budget_check", "Check remaining budget", "finance"),
)


@pytest.fixture
def _patch_registry_available(monkeypatch):
    """Patch the module-level flag and constructor so the registry is 'available'."""
    import shared.a2a_wrapper as mod

    monkeypatch.setattr(mod, "_TOOL_REGISTRY_AVAILABLE", True)
    fake_reg = _FakeRegistry(SAMPLE_SPECS)
    monkeypatch.setattr(mod, "UnifiedToolRegistry", lambda: fake_reg)
    return fake_reg


@pytest.fixture
def _patch_registry_empty(monkeypatch):
    """Registry is available but has no tools."""
    import shared.a2a_wrapper as mod

    monkeypatch.setattr(mod, "_TOOL_REGISTRY_AVAILABLE", True)
    monkeypatch.setattr(mod, "UnifiedToolRegistry", lambda: _FakeRegistry([]))


@pytest.fixture
def _patch_registry_unavailable(monkeypatch):
    """Simulate ToolRegistry import failure."""
    import shared.a2a_wrapper as mod

    monkeypatch.setattr(mod, "_TOOL_REGISTRY_AVAILABLE", False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentCardAutoPopulate:
    """AgentCard capabilities auto-populated from ToolRegistry."""

    @pytest.mark.asyncio
    async def test_agentcard_includes_registry_tools(self, _patch_registry_available):
        """AgentCard endpoint should list tool names from the registry."""
        from shared.a2a_wrapper import AgentCard, create_a2a_app

        card = AgentCard(
            name="test-agent",
            capabilities=["hardcoded_cap"],
        )

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)

        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()

        # Should contain all four registry tools
        assert "web_search" in data["capabilities"]
        assert "email_send" in data["capabilities"]
        assert "lead_score" in data["capabilities"]
        assert "budget_check" in data["capabilities"]
        # Hardcoded cap should be replaced, not merged
        assert len(data["capabilities"]) == 4

    @pytest.mark.asyncio
    async def test_agentcard_fallback_to_hardcoded(self, _patch_registry_empty):
        """When registry is empty, AgentCard should keep its hardcoded capabilities."""
        from shared.a2a_wrapper import AgentCard, create_a2a_app

        card = AgentCard(
            name="test-agent",
            capabilities=["hardcoded_cap_a", "hardcoded_cap_b"],
        )

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)

        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()

        assert data["capabilities"] == ["hardcoded_cap_a", "hardcoded_cap_b"]

    def test_agentcard_caches_capabilities(self, _patch_registry_available, monkeypatch):
        """Repeated requests should use cached capabilities, not re-query registry."""
        import shared.a2a_wrapper as mod
        from shared.a2a_wrapper import AgentCard, create_a2a_app

        card = AgentCard(name="test-agent")

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)

        from starlette.testclient import TestClient

        client = TestClient(app)

        # First request — populates cache
        resp1 = client.get("/.well-known/agent.json")
        assert resp1.status_code == 200

        # Swap registry to empty — cache should still serve old data
        monkeypatch.setattr(mod, "UnifiedToolRegistry", lambda: _FakeRegistry([]))

        resp2 = client.get("/.well-known/agent.json")
        data2 = resp2.json()
        assert "web_search" in data2["capabilities"]

    def test_agentcard_refresh_updates_cache(self, _patch_registry_available, monkeypatch):
        """After TTL expires, refresh should pick up new registry data."""
        import shared.a2a_wrapper as mod
        from shared.a2a_wrapper import AgentCard, _CapabilityCache, create_a2a_app, refresh_capabilities

        card = AgentCard(name="test-agent")

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)
        cache: _CapabilityCache = app.state.capability_cache

        from starlette.testclient import TestClient

        client = TestClient(app)

        # First request populates cache
        client.get("/.well-known/agent.json")
        assert cache.populated

        # Swap registry to different tools
        new_specs = _make_specs(("new_tool", "A brand new tool", "new_cat"))
        monkeypatch.setattr(mod, "UnifiedToolRegistry", lambda: _FakeRegistry(new_specs))

        # Force cache expiry
        cache.invalidate()

        resp = client.get("/.well-known/agent.json")
        data = resp.json()
        assert data["capabilities"] == ["new_tool"]

    @pytest.mark.asyncio
    async def test_agentcard_registry_unavailable(self, _patch_registry_unavailable):
        """When ToolRegistry cannot be imported, AgentCard degrades gracefully."""
        from shared.a2a_wrapper import AgentCard, create_a2a_app

        card = AgentCard(
            name="test-agent",
            capabilities=["fallback_cap"],
        )

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)

        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()

        assert data["capabilities"] == ["fallback_cap"]


class TestCapabilitiesEndpoint:
    """The /a2a/capabilities endpoint should also use registry data."""

    def test_capabilities_endpoint_returns_grouped_tools(self, _patch_registry_available):
        from shared.a2a_wrapper import AgentCard, create_a2a_app

        card = AgentCard(name="test-agent")

        async def noop_handler(text: str) -> str:
            return "ok"

        app = create_a2a_app(card, noop_handler)

        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/a2a/capabilities")
        assert resp.status_code == 200
        data = resp.json()

        # Should have grouped categories
        categories = {item["category"] for item in data["capabilities"]}
        assert "search" in categories
        assert "outreach" in categories
