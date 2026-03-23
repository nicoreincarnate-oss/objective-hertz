"""MCP Client — connects to MCP servers and discovers/calls tools."""

from __future__ import annotations

import itertools
from typing import Any, Dict, List

from openjarvis.mcp.protocol import MCPError, MCPRequest, MCPResponse
from openjarvis.mcp.transport import MCPTransport
from openjarvis.tools._stubs import ToolSpec


class MCPClient:
    """Client that communicates with an MCP server via a transport.

    Parameters
    ----------
    transport:
        The transport layer to use for communication.
    """

    def __init__(self, transport: MCPTransport) -> None:
        self._transport = transport
        self._initialized = False
        self._capabilities: Dict[str, Any] = {}
        self._id_counter = itertools.count(1)

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _send(self, method: str, params: Dict[str, Any] | None = None) -> MCPResponse:
        """Send a request and check for errors."""
        request = MCPRequest(
            method=method,
            params=params or {},
            id=self._next_id(),
        )
        response = self._transport.send(request)
        if response.error is not None:
            raise MCPError(
                code=response.error.get("code", -1),
                message=response.error.get("message", "Unknown error"),
                data=response.error.get("data"),
            )
        return response

    def initialize(self) -> Dict[str, Any]:
        """Perform the MCP initialize handshake.

        Returns the server capabilities.
        """
        response = self._send("initialize")
        self._initialized = True
        self._capabilities = response.result.get("capabilities", {})
        return response.result

    def list_tools(self) -> List[ToolSpec]:
        """Discover available tools from the server.

        Returns a list of ``ToolSpec`` objects.
        """
        response = self._send("tools/list")
        tools = response.result.get("tools", [])
        return [
            ToolSpec(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("inputSchema", {}),
            )
            for t in tools
        ]

    def call_tool(
        self, name: str, arguments: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Call a tool on the server.

        Returns the result dictionary with ``content`` and ``isError`` fields.
        """
        response = self._send(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return response.result

    def close(self) -> None:
        """Close the transport connection."""
        self._transport.close()


__all__ = ["MCPClient"]
