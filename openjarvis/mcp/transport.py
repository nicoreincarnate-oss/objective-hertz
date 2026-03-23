"""MCP transport implementations."""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

from openjarvis.mcp.protocol import MCPRequest, MCPResponse

if TYPE_CHECKING:
    from openjarvis.mcp.server import MCPServer


class MCPTransport(ABC):
    """Abstract transport layer for MCP communication."""

    @abstractmethod
    def send(self, request: MCPRequest) -> MCPResponse:
        """Send a request and return the response."""

    @abstractmethod
    def close(self) -> None:
        """Release transport resources."""


class InProcessTransport(MCPTransport):
    """Direct in-process transport for testing.

    Routes requests directly to an ``MCPServer`` instance without
    serialization overhead.
    """

    def __init__(self, server: MCPServer) -> None:
        self._server = server

    def send(self, request: MCPRequest) -> MCPResponse:
        """Dispatch request directly to the server."""
        return self._server.handle(request)

    def close(self) -> None:
        """No resources to release."""


class StdioTransport(MCPTransport):
    """JSON-RPC over stdin/stdout subprocess transport.

    Launches a subprocess and communicates via JSON lines on
    stdin/stdout.
    """

    def __init__(self, command: List[str]) -> None:
        self._command = command
        self._process: Optional[subprocess.Popen[str]] = None
        self._start()

    def _start(self) -> None:
        """Start the subprocess."""
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def send(self, request: MCPRequest) -> MCPResponse:
        """Write request as JSON line, read response line."""
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("Transport process is not running")

        line = request.to_json() + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

        response_line = proc.stdout.readline()
        if not response_line:
            raise RuntimeError("No response from subprocess")
        return MCPResponse.from_json(response_line.strip())

    def close(self) -> None:
        """Terminate the subprocess."""
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=5)
            self._process = None


class SSETransport(MCPTransport):
    """JSON-RPC over HTTP with Server-Sent Events.

    Sends requests via HTTP POST and reads SSE responses.
    """

    def __init__(self, url: str) -> None:
        self._url = url

    def send(self, request: MCPRequest) -> MCPResponse:
        """Send request via HTTP POST."""
        import httpx

        response = httpx.post(
            self._url,
            json=json.loads(request.to_json()),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return MCPResponse.from_json(response.text)

    def close(self) -> None:
        """No persistent connection to close."""


__all__ = [
    "InProcessTransport",
    "MCPTransport",
    "SSETransport",
    "StdioTransport",
]
