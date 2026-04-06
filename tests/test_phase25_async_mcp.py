"""Tests for Phase 25-03: Async MCP transports.

Covers async send_async() on all transports, MCPClient async methods,
feature-flag gating, and the new HTTPStreamableTransport.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy shared/openjarvis dependencies so that the MCP modules
# can be imported on Python 3.9 without pulling in the full SDK.
# ---------------------------------------------------------------------------

# Save a snapshot of sys.modules BEFORE we install stubs, so we can clean up
# after all tests in this module.
_MODULES_SNAPSHOT = {k: v for k, v in sys.modules.items()}

# Prevent openjarvis top-level __init__ from importing the SDK (needs 3.10+)
_oj_pkg = types.ModuleType("openjarvis")
_oj_pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "openjarvis")]  # type: ignore[attr-defined]
_oj_pkg.__package__ = "openjarvis"
sys.modules.setdefault("openjarvis", _oj_pkg)

# Minimal stubs for transitive imports
_oj_root = os.path.join(os.path.dirname(__file__), "..", "openjarvis")
for _mod_name in (
    "openjarvis.core", "openjarvis.core.events", "openjarvis.core.types",
    "openjarvis.tools", "openjarvis.tools.schema_validator",
):
    if _mod_name not in sys.modules:
        _stub = types.ModuleType(_mod_name)
        # Give sub-packages a __path__ so Python treats them as packages
        # and allows further submodule imports.
        _parts = _mod_name.split(".")
        _stub.__path__ = [os.path.join(_oj_root, *_parts[1:])]  # type: ignore[attr-defined]
        _stub.__package__ = _mod_name  # type: ignore[attr-defined]
        sys.modules[_mod_name] = _stub

# ToolSpec stub (used by MCPClient)
_tools_stubs = types.ModuleType("openjarvis.tools._stubs")


class _ToolSpec:
    def __init__(self, name: str = "", description: str = "", parameters: dict | None = None, **kw):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        # Accept any extra kwargs (requires_confirmation, concurrency_check, metadata, etc.)
        for k, v in kw.items():
            setattr(self, k, v)


_tools_stubs.ToolSpec = _ToolSpec  # type: ignore[attr-defined]
_tools_stubs.BaseTool = type("BaseTool", (), {})  # type: ignore[attr-defined]
_tools_stubs.ToolExecutor = type("ToolExecutor", (), {})  # type: ignore[attr-defined]
_tools_stubs.ToolRegistry = type("ToolRegistry", (), {"register": staticmethod(lambda n: lambda c: c)})  # type: ignore[attr-defined]
_tools_stubs.validate_tool_params = lambda *a, **kw: None  # type: ignore[attr-defined]
sys.modules["openjarvis.tools._stubs"] = _tools_stubs

# Stub httpx if not installed (tests mock all HTTP calls anyway)
_httpx_was_missing = "httpx" not in sys.modules
if _httpx_was_missing:
    _httpx = types.ModuleType("httpx")
    _httpx.AsyncClient = MagicMock  # type: ignore[attr-defined]
    _httpx.post = MagicMock  # type: ignore[attr-defined]
    sys.modules["httpx"] = _httpx
else:
    _httpx = sys.modules["httpx"]

# EventBus / EventType stubs
_events = sys.modules["openjarvis.core.events"]
_events.EventBus = type("EventBus", (), {})  # type: ignore[attr-defined]
_events.EventType = type("EventType", (), {})  # type: ignore[attr-defined]

# ToolCall / ToolContext / ToolResult stubs
_ctypes = sys.modules["openjarvis.core.types"]
_ctypes.ToolCall = type("ToolCall", (), {})  # type: ignore[attr-defined]
_ctypes.ToolContext = type("ToolContext", (), {})  # type: ignore[attr-defined]
_ctypes.ToolResult = type("ToolResult", (), {})  # type: ignore[attr-defined]

# Now import the actual MCP modules
from openjarvis.mcp.client import MCPClient
from openjarvis.mcp.protocol import MCPRequest, MCPResponse
from openjarvis.mcp.transport import (
    HTTPStreamableTransport,
    InProcessTransport,
    MCPTransport,
    SSETransport,
    StdioTransport,
)

# ---------------------------------------------------------------------------
# Module cleanup: restore sys.modules to pre-stub state after all tests run.
# This prevents stub modules from leaking into other test files.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _restore_modules_after_async_mcp_tests():
    """Yield to run all tests, then restore sys.modules to pre-stub state."""
    yield
    # Remove modules that were added by our stubs
    for k in list(sys.modules):
        if k not in _MODULES_SNAPSHOT:
            del sys.modules[k]
    # Restore original modules
    for k, v in _MODULES_SNAPSHOT.items():
        sys.modules[k] = v
    importlib.invalidate_caches()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(method: str = "tools/call") -> MCPRequest:
    return MCPRequest(method=method, params={"name": "echo"}, id=1)


def _make_response(result: dict | None = None) -> MCPResponse:
    return MCPResponse(result=result or {"content": "ok"}, id=1)


# ---------------------------------------------------------------------------
# test_stdio_transport_send_async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stdio_transport_send_async():
    """Async subprocess communication works via send_async."""
    expected = _make_response({"content": "hello"})

    # Patch asyncio.create_subprocess_exec to return a mock process
    mock_proc = AsyncMock()
    mock_proc.returncode = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.write = MagicMock()  # write() is a regular call, not a coroutine
    mock_proc.stdin.drain = AsyncMock()  # drain() is a coroutine
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(
        return_value=(expected.to_json() + "\n").encode()
    )

    with patch("subprocess.Popen"):  # prevent real sync subprocess
        transport = StdioTransport.__new__(StdioTransport)
        transport._command = ["echo"]
        transport._process = None
        transport._async_process = None

    with patch(
        "asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        result = await transport.send_async(_make_request())

    assert result.result == {"content": "hello"}
    mock_proc.stdin.write.assert_called_once()
    mock_proc.stdin.drain.assert_awaited_once()
    mock_proc.stdout.readline.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_inprocess_transport_async — async handler awaited correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inprocess_transport_async():
    """When the server's handle method is async, send_async awaits it."""
    expected = _make_response({"content": "async-ok"})

    server = MagicMock()
    # Make handle an async function
    server.handle = AsyncMock(return_value=expected)

    transport = InProcessTransport(server)
    result = await transport.send_async(_make_request())

    assert result.result == {"content": "async-ok"}
    server.handle.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_inprocess_transport_sync_wrapped — sync handler wrapped in to_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inprocess_transport_sync_wrapped():
    """When the server's handle is sync, send_async calls it and returns directly."""
    expected = _make_response({"content": "sync-wrapped"})

    # Use a real function (not MagicMock) so iscoroutinefunction returns False
    # and the result is not awaitable
    class FakeServer:
        def handle(self, request):
            return expected

    server = FakeServer()
    transport = InProcessTransport(server)  # type: ignore[arg-type]
    result = await transport.send_async(_make_request())

    assert result.result == {"content": "sync-wrapped"}


# ---------------------------------------------------------------------------
# test_client_call_tool_async — async tool call returns result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_call_tool_async():
    """MCPClient.call_tool_async returns the tool result via async transport."""
    expected = _make_response({"content": [{"type": "text", "text": "done"}]})

    transport = MagicMock()
    transport.send_async = AsyncMock(return_value=expected)
    transport.send = MagicMock(return_value=expected)
    transport.close = MagicMock()
    transport.close_async = AsyncMock()

    # Enable the async flag for this test
    with patch.dict(os.environ, {"ANATOMY_ASYNC_MCP": "true"}):
        # Re-import to pick up the flag (or patch the module-level variable)
        import openjarvis.mcp.client as client_mod
        original = client_mod._ASYNC_MCP_ENABLED
        client_mod._ASYNC_MCP_ENABLED = True
        try:
            client = MCPClient(transport)
            result = await client.call_tool_async("echo", {"msg": "hi"})
        finally:
            client_mod._ASYNC_MCP_ENABLED = original

    assert result == {"content": [{"type": "text", "text": "done"}]}


# ---------------------------------------------------------------------------
# test_client_list_tools_async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_list_tools_async():
    """MCPClient.list_tools_async returns ToolSpec objects."""
    tools_response = MCPResponse(
        result={
            "tools": [
                {"name": "echo", "description": "Echo tool", "inputSchema": {}},
                {"name": "add", "description": "Add numbers", "inputSchema": {"type": "object"}},
            ]
        },
        id=1,
    )

    transport = MagicMock()
    transport.send_async = AsyncMock(return_value=tools_response)
    transport.send = MagicMock(return_value=tools_response)
    transport.close = MagicMock()
    transport.close_async = AsyncMock()

    import openjarvis.mcp.client as client_mod
    original = client_mod._ASYNC_MCP_ENABLED
    client_mod._ASYNC_MCP_ENABLED = True
    try:
        client = MCPClient(transport)
        tools = await client.list_tools_async()
    finally:
        client_mod._ASYNC_MCP_ENABLED = original

    assert len(tools) == 2
    assert tools[0].name == "echo"
    assert tools[1].name == "add"


# ---------------------------------------------------------------------------
# test_flag_off_uses_sync — existing sync paths work when flag is off
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_uses_sync():
    """When ANATOMY_ASYNC_MCP is off, call_tool_async falls back to sync _send."""
    expected = _make_response({"content": "sync-fallback"})

    transport = MagicMock(spec=MCPTransport)
    transport.send = MagicMock(return_value=expected)
    # send_async should NOT be called when flag is off
    transport.send_async = AsyncMock()

    import openjarvis.mcp.client as client_mod
    original = client_mod._ASYNC_MCP_ENABLED
    client_mod._ASYNC_MCP_ENABLED = False
    try:
        client = MCPClient(transport)
        result = await client.call_tool_async("echo", {"msg": "hi"})
    finally:
        client_mod._ASYNC_MCP_ENABLED = original

    assert result == {"content": "sync-fallback"}
    # Sync send was called (via _send -> to_thread), NOT send_async
    transport.send_async.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_http_streamable_transport — basic HTTP POST + session management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_streamable_transport():
    """HTTPStreamableTransport sends requests with session ID management."""
    import httpx as _httpx_mod

    expected = _make_response({"content": "streamable-ok"})

    transport = HTTPStreamableTransport("http://localhost:8080/mcp")
    assert transport.session_id is None

    # Mock httpx.AsyncClient
    mock_response = MagicMock()
    mock_response.text = expected.to_json()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {"mcp-session-id": "sess-abc-123"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    original_ac = getattr(_httpx_mod, "AsyncClient", None)
    _httpx_mod.AsyncClient = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]
    try:
        result = await transport.send_async(_make_request())
    finally:
        if original_ac is not None:
            _httpx_mod.AsyncClient = original_ac  # type: ignore[attr-defined]

    assert result.result == {"content": "streamable-ok"}
    # Session ID should be captured from response headers
    assert transport.session_id == "sess-abc-123"

    # Second request should include the session ID in headers
    _httpx_mod.AsyncClient = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]
    try:
        await transport.send_async(_make_request())
    finally:
        if original_ac is not None:
            _httpx_mod.AsyncClient = original_ac  # type: ignore[attr-defined]

    # Verify the second call included the session header
    call_args = mock_client.post.call_args
    assert call_args.kwargs["headers"]["Mcp-Session-Id"] == "sess-abc-123"


# ---------------------------------------------------------------------------
# test_http_streamable_transport_sync
# ---------------------------------------------------------------------------

def test_http_streamable_transport_sync():
    """HTTPStreamableTransport sync send also manages sessions."""
    import httpx as _httpx_mod

    expected = _make_response({"content": "sync-ok"})

    transport = HTTPStreamableTransport(
        "http://localhost:8080/mcp", session_id="existing-session"
    )
    assert transport.session_id == "existing-session"

    mock_response = MagicMock()
    mock_response.text = expected.to_json()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {"mcp-session-id": "new-session"}

    original_post = getattr(_httpx_mod, "post", None)
    mock_post = MagicMock(return_value=mock_response)
    _httpx_mod.post = mock_post  # type: ignore[attr-defined]
    try:
        result = transport.send(_make_request())
    finally:
        if original_post is not None:
            _httpx_mod.post = original_post  # type: ignore[attr-defined]

    assert result.result == {"content": "sync-ok"}
    assert transport.session_id == "new-session"
    # Verify the request included the old session header
    call_headers = mock_post.call_args.kwargs["headers"]
    assert call_headers["Mcp-Session-Id"] == "existing-session"


# ---------------------------------------------------------------------------
# test_base_transport_default_send_async — wraps sync via to_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_transport_default_send_async():
    """MCPTransport base class send_async wraps sync send in to_thread."""

    class SyncOnly(MCPTransport):
        def send(self, request: MCPRequest) -> MCPResponse:
            return _make_response({"content": "base-default"})

        def close(self) -> None:
            pass

    transport = SyncOnly()
    result = await transport.send_async(_make_request())
    assert result.result == {"content": "base-default"}


# ---------------------------------------------------------------------------
# test_sse_transport_send_async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sse_transport_send_async():
    """SSETransport async path uses httpx.AsyncClient."""
    import httpx as _httpx_mod

    expected = _make_response({"content": "sse-async"})

    transport = SSETransport("http://localhost:9000/sse")

    mock_response = MagicMock()
    mock_response.text = expected.to_json()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    original_ac = getattr(_httpx_mod, "AsyncClient", None)
    _httpx_mod.AsyncClient = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]
    try:
        result = await transport.send_async(_make_request())
    finally:
        if original_ac is not None:
            _httpx_mod.AsyncClient = original_ac  # type: ignore[attr-defined]

    assert result.result == {"content": "sse-async"}
    mock_client.post.assert_awaited_once()
