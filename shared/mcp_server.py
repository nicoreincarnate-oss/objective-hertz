"""Perseus MCP Server -- exposes all registered tools via Model Context Protocol.

Starts an MCP server (InProcess transport) during daemon boot.
All tools registered in the OJ ToolRegistry become MCP-discoverable.
External agents and tools can connect to discover and call Perseus tools.

Usage:
    from shared.mcp_server import start_mcp_server, get_mcp_client

    # At daemon startup (after OJ tools are loaded):
    ok = await start_mcp_server()

    # Agents that need tool discovery:
    client = await get_mcp_client()
    tools = client.list_tools()
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("perseus.mcp_server")

_server: Any = None
_transport: Any = None
_client: Any = None


async def start_mcp_server() -> bool:
    """Start the Perseus MCP server with InProcess transport.

    Registers all tools from the OJ ToolRegistry / auto-discovery.
    Called during orchestrator/daemon startup after tools are loaded.

    Returns True on success, False if the MCP module is unavailable.
    """
    global _server, _transport

    try:
        from openjarvis.mcp.server import MCPServer
        from openjarvis.mcp.transport import InProcessTransport
    except ImportError:
        logger.warning(
            "openjarvis.mcp module not available -- MCP server disabled. "
            "Tool discovery will fall back to direct OJ registry lookups."
        )
        return False

    try:
        # MCPServer auto-discovers all OJ tools when tools=None
        _server = MCPServer(tools=None)
        _transport = InProcessTransport(_server)

        tool_count = len(_server.get_tools())
        logger.info(
            "Perseus MCP server started with %d tools via InProcess transport",
            tool_count,
        )
        return True

    except Exception as exc:
        logger.error("Failed to start MCP server: %s", exc, exc_info=True)
        _server = None
        _transport = None
        return False


async def stop_mcp_server() -> None:
    """Shut down the MCP server and release resources."""
    global _server, _transport, _client

    if _client is not None:
        try:
            _client.close()
        except Exception as exc:
            logger.debug("Error closing MCP client: %s", exc)
        _client = None

    if _transport is not None:
        try:
            _transport.close()
        except Exception as exc:
            logger.debug("Error closing MCP transport: %s", exc)
        _transport = None

    _server = None
    logger.info("Perseus MCP server stopped")


def get_mcp_server():
    """Get the running MCPServer instance (for in-process use).

    Returns None if the server has not been started.
    """
    return _server


async def get_mcp_client():
    """Get an MCP client connected to the local server.

    Creates a new client on first call and reuses it thereafter.
    The client is initialized (handshake completed) and ready to use.

    Returns None if the server is not running.
    """
    global _client

    if _server is None or _transport is None:
        logger.warning("MCP server not running -- cannot create client")
        return None

    if _client is not None:
        return _client

    try:
        from openjarvis.mcp.client import MCPClient
        from openjarvis.mcp.transport import InProcessTransport

        # Each client gets its own transport pointing to the same server
        client_transport = InProcessTransport(_server)
        _client = MCPClient(client_transport)
        _client.initialize()
        return _client
    except Exception as exc:
        logger.error("Failed to create MCP client: %s", exc, exc_info=True)
        return None


async def refresh_tools() -> int:
    """Re-scan tools and update MCP server registrations.

    Called when new tools are dynamically added at runtime.
    Rebuilds the server with fresh auto-discovery.

    Returns the new tool count, or -1 on failure.
    """
    global _server, _transport, _client

    if _server is None:
        logger.warning("MCP server not running -- nothing to refresh")
        return -1

    try:
        from openjarvis.mcp.server import MCPServer
        from openjarvis.mcp.transport import InProcessTransport

        # Close existing client so it reconnects on next get_mcp_client()
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None

        # Rebuild server with fresh tool discovery
        _server = MCPServer(tools=None)
        _transport = InProcessTransport(_server)

        tool_count = len(_server.get_tools())
        logger.info("MCP server refreshed -- now exposing %d tools", tool_count)
        return tool_count

    except Exception as exc:
        logger.error("Failed to refresh MCP tools: %s", exc, exc_info=True)
        return -1


def register_tools(tools: list) -> None:
    """Register additional tools into the running MCP server.

    This is a lighter alternative to refresh_tools() when you already
    have the tool instances and don't need full re-discovery.
    """
    if _server is None:
        logger.warning("MCP server not running -- cannot register tools")
        return

    for tool in tools:
        name = tool.spec.name
        if name not in _server._tools:
            _server._tools[name] = tool
            # Also register in the executor so tools/call works
            _server._executor._tools[name] = tool
            logger.debug("Registered tool '%s' in MCP server", name)


__all__ = [
    "get_mcp_client",
    "get_mcp_server",
    "refresh_tools",
    "register_tools",
    "start_mcp_server",
    "stop_mcp_server",
]
