"""MCP Proof-of-Concept — Phase 0b, Task 6.

Validates the three-system convergence strategy:
  1. OJ BaseTool  -> MCP server -> MCP client discover + call
  2. Plain Python function -> HandlerToolAdapter -> MCP server -> MCP client
  3. SKILL.md as MCP resource (gap documentation)
  4. Cross-system discovery (multiple tools from different sources)

This PoC uses the InProcess transport only (no subprocess, no network).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.mcp.client import MCPClient
from openjarvis.mcp.protocol import MCPRequest, MCPResponse
from openjarvis.mcp.server import MCPServer
from openjarvis.mcp.transport import InProcessTransport
from openjarvis.tools._stubs import BaseTool, ToolSpec


# ---------------------------------------------------------------------------
# Test fixtures: concrete BaseTool implementations
# ---------------------------------------------------------------------------


class AddNumbersTool(BaseTool):
    """Simple tool that adds two numbers. Used to validate OJ BaseTool -> MCP."""

    tool_id = "add_numbers"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="add_numbers",
            description="Add two numbers and return the sum.",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"},
                },
                "required": ["a", "b"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        a = params.get("a", 0)
        b = params.get("b", 0)
        result = a + b
        return ToolResult(
            tool_name="add_numbers",
            content=str(result),
            success=True,
        )


class GreetTool(BaseTool):
    """Simple greeting tool. Used as a second tool for cross-system discovery."""

    tool_id = "greet"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="greet",
            description="Return a greeting for the given name.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name to greet"},
                },
                "required": ["name"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        name = params.get("name", "world")
        return ToolResult(
            tool_name="greet",
            content=f"Hello, {name}!",
            success=True,
        )


# ---------------------------------------------------------------------------
# HandlerToolAdapter — wraps a plain Python function as a BaseTool
# ---------------------------------------------------------------------------


class HandlerToolAdapter(BaseTool):
    """Adapts a plain Python function into a BaseTool.

    This is the adapter pattern from the ToolSpec design (0b-T5).
    It takes a callable and a ToolSpec and bridges the two worlds:
    OJ's BaseTool interface and arbitrary Python functions.
    """

    def __init__(self, fn: Any, tool_spec: ToolSpec) -> None:
        self._fn = fn
        self._spec = tool_spec
        self.tool_id = tool_spec.name

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **params: Any) -> ToolResult:
        try:
            result = self._fn(**params)
            return ToolResult(
                tool_name=self._spec.name,
                content=str(result),
                success=True,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self._spec.name,
                content=f"Error: {exc}",
                success=False,
            )


# ---------------------------------------------------------------------------
# Helper: create server + client pair over InProcess transport
# ---------------------------------------------------------------------------


def _make_client(tools: list[BaseTool]) -> MCPClient:
    """Create an MCPServer with the given tools and return a connected MCPClient."""
    server = MCPServer(tools=tools)
    transport = InProcessTransport(server)
    client = MCPClient(transport)
    client.initialize()
    return client


# ===========================================================================
# TESTS
# ===========================================================================


class TestOJToolInMCP:
    """PoC 1: Register an OJ BaseTool, expose via MCP, discover + call."""

    def test_oj_tool_registers_in_mcp(self) -> None:
        """An OJ BaseTool can be registered in MCPServer without errors."""
        tool = AddNumbersTool()
        server = MCPServer(tools=[tool])
        # Server should have exactly one tool registered
        assert len(server.get_tools()) == 1
        assert server.get_tools()[0].spec.name == "add_numbers"

    def test_oj_tool_discoverable_via_mcp(self) -> None:
        """An OJ BaseTool registered in MCPServer is discoverable via tools/list."""
        client = _make_client([AddNumbersTool()])
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "add_numbers"
        assert tools[0].description == "Add two numbers and return the sum."
        # Verify the inputSchema was passed through
        assert "properties" in tools[0].parameters
        assert "a" in tools[0].parameters["properties"]
        assert "b" in tools[0].parameters["properties"]
        client.close()

    def test_oj_tool_callable_via_mcp(self) -> None:
        """An OJ BaseTool can be called via MCP tools/call and returns correct result."""
        client = _make_client([AddNumbersTool()])
        result = client.call_tool("add_numbers", {"a": 3, "b": 7})

        # MCP result format: {"content": [{"type": "text", "text": "..."}], "isError": false}
        assert result["isError"] is False
        content_items = result["content"]
        assert len(content_items) == 1
        assert content_items[0]["type"] == "text"
        assert content_items[0]["text"] == "10"
        client.close()

    def test_tool_call_returns_correct_result(self) -> None:
        """Verify arithmetic correctness across multiple inputs."""
        client = _make_client([AddNumbersTool()])

        test_cases = [
            (0, 0, "0"),
            (1, -1, "0"),
            (100, 200, "300"),
            (-5, -10, "-15"),
            (3.14, 2.86, "6.0"),
        ]
        for a, b, expected in test_cases:
            result = client.call_tool("add_numbers", {"a": a, "b": b})
            assert result["isError"] is False
            assert result["content"][0]["text"] == expected, (
                f"add_numbers({a}, {b}) expected {expected}, "
                f"got {result['content'][0]['text']}"
            )
        client.close()


class TestPythonFunctionAsMCPTool:
    """PoC 2: Wrap a plain Python function via HandlerToolAdapter, expose via MCP."""

    @staticmethod
    def _multiply(a: int, b: int) -> int:
        return a * b

    def test_python_function_as_mcp_tool(self) -> None:
        """A plain Python function wrapped in HandlerToolAdapter is callable via MCP."""
        multiply_spec = ToolSpec(
            name="multiply",
            description="Multiply two integers.",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "First factor"},
                    "b": {"type": "integer", "description": "Second factor"},
                },
                "required": ["a", "b"],
            },
        )
        adapted_tool = HandlerToolAdapter(self._multiply, multiply_spec)
        client = _make_client([adapted_tool])

        # Discover
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "multiply"

        # Call
        result = client.call_tool("multiply", {"a": 6, "b": 7})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "42"
        client.close()

    def test_handler_adapter_error_handling(self) -> None:
        """HandlerToolAdapter surfaces errors from the wrapped function as isError=True."""

        def bad_fn(**kwargs: Any) -> None:
            raise ValueError("intentional test error")

        adapted = HandlerToolAdapter(
            bad_fn,
            ToolSpec(name="bad_tool", description="Always fails."),
        )
        client = _make_client([adapted])
        result = client.call_tool("bad_tool", {})
        # The tool itself returns success=False, which MCPServer maps to isError=True
        assert result["isError"] is True
        assert "intentional test error" in result["content"][0]["text"]
        client.close()


class TestCrossSystemDiscovery:
    """PoC 4: Multiple tools from different sources are all discoverable."""

    def test_multiple_tools_discoverable(self) -> None:
        """A single MCP client discovers tools from all registered sources."""

        def concat(a: str, b: str) -> str:
            return a + b

        concat_adapter = HandlerToolAdapter(
            concat,
            ToolSpec(
                name="concat",
                description="Concatenate two strings.",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                    },
                    "required": ["a", "b"],
                },
            ),
        )

        # Mix of native BaseTool and HandlerToolAdapter
        all_tools: list[BaseTool] = [
            AddNumbersTool(),
            GreetTool(),
            concat_adapter,
        ]
        client = _make_client(all_tools)
        discovered = client.list_tools()

        discovered_names = {t.name for t in discovered}
        assert discovered_names == {"add_numbers", "greet", "concat"}

        # Verify each tool is independently callable
        r1 = client.call_tool("add_numbers", {"a": 1, "b": 2})
        assert r1["content"][0]["text"] == "3"

        r2 = client.call_tool("greet", {"name": "Perseus"})
        assert r2["content"][0]["text"] == "Hello, Perseus!"

        r3 = client.call_tool("concat", {"a": "foo", "b": "bar"})
        assert r3["content"][0]["text"] == "foobar"

        client.close()

    def test_tool_descriptions_preserved(self) -> None:
        """Tool descriptions survive the BaseTool -> MCP -> ToolSpec round-trip."""
        tools = [AddNumbersTool(), GreetTool()]
        client = _make_client(tools)
        discovered = {t.name: t for t in client.list_tools()}

        assert discovered["add_numbers"].description == "Add two numbers and return the sum."
        assert discovered["greet"].description == "Return a greeting for the given name."
        client.close()


class TestMCPProtocolCompliance:
    """Verify MCP protocol basics work end-to-end via InProcess transport."""

    def test_initialize_handshake(self) -> None:
        """Client initialize returns protocol version and capabilities."""
        server = MCPServer(tools=[AddNumbersTool()])
        transport = InProcessTransport(server)
        client = MCPClient(transport)

        result = client.initialize()
        assert result["protocolVersion"] == "2025-11-25"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "openjarvis"
        client.close()

    def test_unknown_tool_returns_error(self) -> None:
        """Calling a nonexistent tool returns an MCP error."""
        from openjarvis.mcp.protocol import MCPError

        client = _make_client([AddNumbersTool()])
        with pytest.raises(MCPError) as exc_info:
            client.call_tool("nonexistent_tool", {})
        assert "Unknown tool" in str(exc_info.value)
        client.close()

    def test_unknown_method_returns_error(self) -> None:
        """Sending an unknown method returns METHOD_NOT_FOUND."""
        from openjarvis.mcp.protocol import MCPError

        server = MCPServer(tools=[])
        transport = InProcessTransport(server)
        client = MCPClient(transport)
        client.initialize()

        with pytest.raises(MCPError) as exc_info:
            client._send("resources/list")  # not implemented
        assert exc_info.value.code == -32601  # METHOD_NOT_FOUND
        client.close()


class TestSKILLMDAsResource:
    """PoC 3: SKILL.md as MCP resource.

    FINDING: MCP resources are NOT supported by the current MCPServer.
    The server only handles: initialize, tools/list, tools/call.
    Sending resources/list returns METHOD_NOT_FOUND (-32601).

    This is a known gap from the MCP audit (0a-T4). The MCP spec defines
    resources as a separate capability, but the OJ server has not implemented
    it. To support SKILL.md files as MCP resources, the server needs:
      1. A resources/list handler returning resource metadata
      2. A resources/read handler returning resource content
      3. The initialize response should advertise {"resources": {}} capability

    FIX NEEDED: Add resources/list and resources/read handlers to MCPServer.
    Estimated effort: ~50 LOC. Not blocking for tool convergence, but needed
    for skill/prompt sharing across MCP boundaries.
    """

    def test_resources_not_supported_documents_gap(self) -> None:
        """Confirm resources/list returns METHOD_NOT_FOUND (expected gap)."""
        from openjarvis.mcp.protocol import MCPError

        server = MCPServer(tools=[])
        transport = InProcessTransport(server)
        client = MCPClient(transport)
        client.initialize()

        with pytest.raises(MCPError) as exc_info:
            client._send("resources/list")

        # This documents the gap: resources are not supported
        assert exc_info.value.code == -32601
        assert "Unknown method" in exc_info.value.message
        client.close()

    def test_resources_read_not_supported_documents_gap(self) -> None:
        """Confirm resources/read returns METHOD_NOT_FOUND (expected gap)."""
        from openjarvis.mcp.protocol import MCPError

        server = MCPServer(tools=[])
        transport = InProcessTransport(server)
        client = MCPClient(transport)
        client.initialize()

        with pytest.raises(MCPError) as exc_info:
            client._send("resources/read", {"uri": "skill://test/SKILL.md"})

        assert exc_info.value.code == -32601
        client.close()
