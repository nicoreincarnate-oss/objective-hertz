"""Tests for shared/register_tools.py and CapabilityRouter + ToolRegistry integration."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.tool_registry import UnifiedToolRegistry, get_registry, ToolType


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the singleton registry before each test."""
    UnifiedToolRegistry.reset()
    yield
    UnifiedToolRegistry.reset()


# ---------------------------------------------------------------------------
# Test 1: register_all_tools registers 20+ tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_all_tools_count():
    """Verify that register_all_tools registers at least 20 tools."""
    from shared.register_tools import register_all_tools

    count = await register_all_tools()
    registry = get_registry()

    # We register 2 apify + 2 tavily + 2 twentyfirst + 3 firecrawl
    # + 5 instantly + 2 n8n + 5 recraft + 3 claude_code + 3 sandbox
    # + 2 visual_qa + 1 browser_use = 30 tools (when imports succeed)
    # Some may fail if packages are not installed, but the core
    # Python-only tools (firecrawl, instantly wrappers, sandbox) should register.
    assert count >= 20, f"Expected 20+ tools registered, got {count}"
    assert len(registry) >= 20


# ---------------------------------------------------------------------------
# Test 2: Apify tools are registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apify_tools_registered():
    """Verify Apify discover_leads and enrich_emails are registered."""
    from shared.register_tools import register_all_tools

    await register_all_tools()
    registry = get_registry()

    apify_discover = registry.find("apify_discover_leads")
    assert apify_discover is not None, "apify_discover_leads not registered"
    assert apify_discover.tool_type == ToolType.HANDLER
    assert "APIFY_API_TOKEN" in apify_discover.requires_env
    assert apify_discover.category == "lead_discovery"

    apify_enrich = registry.find("apify_enrich_emails")
    assert apify_enrich is not None, "apify_enrich_emails not registered"
    assert "APIFY_API_TOKEN" in apify_enrich.requires_env


# ---------------------------------------------------------------------------
# Test 3: Tavily tools are registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tavily_tools_registered():
    """Verify Tavily search and research_business are registered."""
    from shared.register_tools import register_all_tools

    await register_all_tools()
    registry = get_registry()

    tavily_search = registry.find("tavily_search")
    assert tavily_search is not None, "tavily_search not registered"
    assert tavily_search.tool_type == ToolType.HANDLER
    assert "TAVILY_API_KEY" in tavily_search.requires_env
    assert tavily_search.category == "search"

    tavily_research = registry.find("tavily_research_business")
    assert tavily_research is not None, "tavily_research_business not registered"


# ---------------------------------------------------------------------------
# Test 4: Tools have env requirements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_have_env_requirements():
    """Verify that API-dependent tools declare their env requirements."""
    from shared.register_tools import register_all_tools

    await register_all_tools()
    registry = get_registry()

    # Map of tool name -> expected env var
    expected_env = {
        "apify_discover_leads": "APIFY_API_TOKEN",
        "tavily_search": "TAVILY_API_KEY",
        "firecrawl_scrape": "FIRECRAWL_API_KEY",
        "recraft_generate_image": "RECRAFT_API_KEY",
        "claude_code_run_task": "ANTHROPIC_API_KEY",
        "twentyfirst_generate_component": "TWENTYFIRST_API_KEY",
    }

    for tool_name, expected_var in expected_env.items():
        spec = registry.find(tool_name)
        if spec is None:
            # Tool may not be importable in test env — skip gracefully
            continue
        assert expected_var in spec.requires_env, (
            f"Tool '{tool_name}' should require {expected_var}, "
            f"but requires_env={spec.requires_env}"
        )


# ---------------------------------------------------------------------------
# Test 5: Tools searchable by tag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_searchable_by_tag():
    """Verify tools can be discovered by tag search."""
    from shared.register_tools import register_all_tools

    await register_all_tools()
    registry = get_registry()

    # Search for email-related tools
    email_tools = registry.search(tags=["email"])
    assert len(email_tools) >= 1, "Should find at least 1 email tool"

    # Search for search-related tools
    search_tools = registry.search(tags=["search"])
    assert len(search_tools) >= 1, "Should find at least 1 search tool"

    # Search for site_building-related tools
    site_tools = registry.search(tags=["site_building"])
    assert len(site_tools) >= 1, "Should find at least 1 site building tool"

    # Search by query string
    recraft_tools = registry.search(query="recraft")
    assert len(recraft_tools) >= 1, "Should find at least 1 recraft tool"


# ---------------------------------------------------------------------------
# Test 6: CapabilityRouter uses ToolRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_router_uses_registry():
    """Verify CapabilityRouter checks ToolRegistry before A2A discovery."""
    from shared.register_tools import register_all_tools

    await register_all_tools()

    # Patch USE_TOOL_REGISTRY to True and provide a fake agent_urls so
    # A2A discovery doesn't actually hit the network.
    import shared.capability_router as cr_module

    original_flag = cr_module.USE_TOOL_REGISTRY
    cr_module.USE_TOOL_REGISTRY = True

    # Set env vars so tools pass env_satisfied() check
    env_patches = {
        "TAVILY_API_KEY": "test-key",
    }
    original_env = {k: os.environ.get(k) for k in env_patches}

    try:
        os.environ.update(env_patches)

        from shared.capability_router import CapabilityRouter

        router = CapabilityRouter(agent_urls={})

        # "tavily_search" is a registered tool name — should route to it
        result = await router.route("tavily_search")
        assert result is not None, "Router should find tavily_search in ToolRegistry"
        assert result.startswith("tool:"), f"Expected 'tool:' prefix, got '{result}'"

        # route_to_tool should return metadata (doesn't require env_satisfied)
        meta = await router.route_to_tool("tavily_search")
        assert meta is not None
        assert meta["source"] == "tool_registry"
        assert meta["tool_name"] == "tavily_search"

    finally:
        cr_module.USE_TOOL_REGISTRY = original_flag
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Test 7: CapabilityRouter falls back to A2A when registry disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_router_fallback_to_a2a():
    """Verify CapabilityRouter falls back to static routing when ToolRegistry is off."""
    import shared.capability_router as cr_module

    original_flag = cr_module.USE_TOOL_REGISTRY
    cr_module.USE_TOOL_REGISTRY = False

    try:
        from shared.capability_router import CapabilityRouter

        router = CapabilityRouter(agent_urls={})

        # "lead_discovery" is in static TASK_ROUTING -> "titan"
        result = await router.route("lead_discovery")
        assert result == "titan", f"Expected 'titan' from static routing, got '{result}'"

        # route_to_tool should return None when disabled
        meta = await router.route_to_tool("tavily_search")
        assert meta is None, "route_to_tool should return None when USE_TOOL_REGISTRY=false"

        # Unknown task should return None
        result = await router.route("nonexistent_task_xyz")
        assert result is None

    finally:
        cr_module.USE_TOOL_REGISTRY = original_flag
