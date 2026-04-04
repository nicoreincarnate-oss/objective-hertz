"""Tests for shared/mcp_server.py -- Perseus MCP server integration.

Validates:
  1. Server starts successfully with auto-discovered tools
  2. Graceful degradation when OJ MCP module is unavailable
  3. Client creation after server start
  4. Client discovers tools via MCP protocol
  5. Dynamic tool refresh adds new tools
  6. Stop cleans up all resources
"""

from __future__ import annotations

import sys
from typing import Any
from unittest import mock

import pytest

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


# ---------------------------------------------------------------------------
# Test tool fixture
# ---------------------------------------------------------------------------


class EchoTool(BaseTool):
    """Simple echo tool for testing."""

    tool_id = "echo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="Echo the input message.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"},
                },
                "required": ["message"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        msg = params.get("message", "")
        return ToolResult(tool_name="echo", content=msg, success=True)


class ReverseTool(BaseTool):
    """Reverse a string -- used for refresh testing."""

    tool_id = "reverse"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="reverse",
            description="Reverse the input string.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to reverse"},
                },
                "required": ["text"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        text = params.get("text", "")
        return ToolResult(tool_name="reverse", content=text[::-1], success=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_module_state():
    """Reset the shared.mcp_server module-level singletons."""
    import shared.mcp_server as mod
    mod._server = None
    mod._transport = None
    mod._client = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure clean module state before and after each test."""
    _reset_module_state()
    yield
    _reset_module_state()


@pytest.mark.asyncio
async def test_start_server_success():
    """MCP server starts and exposes tools from auto-discovery."""
    from shared.mcp_server import start_mcp_server, get_mcp_server

    # Patch MCPServer to use our known tool instead of real auto-discovery
    with mock.patch(
        "openjarvis.mcp.server.MCPServer._auto_discover_tools",
        return_value=[EchoTool()],
    ):
        result = await start_mcp_server()

    assert result is True
    server = get_mcp_server()
    assert server is not None
    assert len(server.get_tools()) == 1
    assert server.get_tools()[0].spec.name == "echo"


@pytest.mark.asyncio
async def test_start_server_no_oj():
    """Graceful degradation when openjarvis.mcp module is unavailable."""
    from shared.mcp_server import start_mcp_server, get_mcp_server

    # Simulate ImportError for the MCP module
    with mock.patch.dict(sys.modules, {"openjarvis.mcp.server": None, "openjarvis.mcp.transport": None}):
        # The import inside start_mcp_server will raise ImportError
        # because we set the module to None in sys.modules
        _reset_module_state()
        # Need to reload to pick up the patched sys.modules
        import importlib
        import shared.mcp_server as mod
        # Directly test the import guard path
        original_server = mod._server
        try:
            # Force the import to fail by temporarily hiding the module
            real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
            original_modules = {}
            for key in list(sys.modules.keys()):
                if key.startswith("openjarvis.mcp"):
                    original_modules[key] = sys.modules.pop(key)

            def fake_import(name, *args, **kwargs):
                if name in ("openjarvis.mcp.server", "openjarvis.mcp.transport"):
                    raise ImportError(f"No module named '{name}'")
                return real_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                result = await start_mcp_server()
        finally:
            # Restore modules
            sys.modules.update(original_modules)

    assert result is False
    assert get_mcp_server() is None


@pytest.mark.asyncio
async def test_get_client_after_start():
    """get_mcp_client returns an initialized client after server start."""
    from shared.mcp_server import start_mcp_server, get_mcp_client

    with mock.patch(
        "openjarvis.mcp.server.MCPServer._auto_discover_tools",
        return_value=[EchoTool()],
    ):
        await start_mcp_server()

    client = await get_mcp_client()
    assert client is not None
    # Client should be initialized (handshake done)
    assert client._initialized is True


@pytest.mark.asyncio
async def test_client_discovers_tools():
    """Client discovers all tools exposed by the MCP server."""
    from shared.mcp_server import start_mcp_server, get_mcp_client

    with mock.patch(
        "openjarvis.mcp.server.MCPServer._auto_discover_tools",
        return_value=[EchoTool()],
    ):
        await start_mcp_server()

    client = await get_mcp_client()
    tools = client.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].description == "Echo the input message."
    assert "message" in tools[0].parameters.get("properties", {})

    # Also verify the tool is callable
    result = client.call_tool("echo", {"message": "hello"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_refresh_adds_new_tool():
    """refresh_tools() picks up newly registered tools."""
    from shared.mcp_server import (
        start_mcp_server,
        get_mcp_client,
        register_tools,
        get_mcp_server,
    )

    with mock.patch(
        "openjarvis.mcp.server.MCPServer._auto_discover_tools",
        return_value=[EchoTool()],
    ):
        await start_mcp_server()

    server = get_mcp_server()
    assert len(server.get_tools()) == 1

    # Dynamically add a new tool
    register_tools([ReverseTool()])

    assert len(server.get_tools()) == 2

    # Get a fresh client (old one cached, need to verify via new client)
    client = await get_mcp_client()
    tools = client.list_tools()
    tool_names = {t.name for t in tools}
    assert "echo" in tool_names
    assert "reverse" in tool_names

    # Call the new tool
    result = client.call_tool("reverse", {"text": "abcd"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "dcba"


@pytest.mark.asyncio
async def test_stop_cleans_up():
    """stop_mcp_server clears server, transport, and client."""
    from shared.mcp_server import (
        start_mcp_server,
        stop_mcp_server,
        get_mcp_server,
        get_mcp_client,
    )

    with mock.patch(
        "openjarvis.mcp.server.MCPServer._auto_discover_tools",
        return_value=[EchoTool()],
    ):
        await start_mcp_server()

    # Create a client first so it gets cached
    client = await get_mcp_client()
    assert client is not None

    # Stop everything
    await stop_mcp_server()

    assert get_mcp_server() is None

    # Client should now return None since server is gone
    import shared.mcp_server as mod
    assert mod._client is None
    assert mod._transport is None
    assert mod._server is None
