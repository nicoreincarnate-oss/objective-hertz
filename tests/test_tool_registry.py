"""Tests for shared/tool_registry.py — Unified ToolRegistry.

Covers: singleton pattern, registration, discovery, search, filtering,
decorator, adapters, execution dispatch, budget gating, logging middleware,
and auto-scan.
"""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.tool_registry import (
    HandlerToolAdapter,
    MCPRemoteToolAdapter,
    SkillToolAdapter,
    ToolSpec,
    ToolType,
    UnifiedBaseTool,
    UnifiedToolRegistry,
    _fn_to_json_schema,
    budget_gate_middleware,
    budget_log_middleware,
    get_registry,
    register_tool,
    _PENDING_REGISTRATIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the singleton before and after every test."""
    UnifiedToolRegistry.reset()
    _PENDING_REGISTRATIONS.clear()
    yield
    UnifiedToolRegistry.reset()
    _PENDING_REGISTRATIONS.clear()


class DummyTool(UnifiedBaseTool):
    """Minimal tool for testing."""

    def __init__(
        self,
        name: str = "dummy",
        description: str = "A dummy tool",
        tags: list[str] | None = None,
        requires_env: list[str] | None = None,
        category: str = "test",
        cost: float = 0.0,
        tool_type: ToolType = ToolType.NATIVE,
    ):
        self._spec = ToolSpec(
            name=name,
            description=description,
            tags=tags or [],
            requires_env=requires_env or [],
            category=category,
            estimated_cost_per_call=cost,
            tool_type=tool_type,
        )
        self.tool_id = f"test:{name}"

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **params: Any):
        from shared.tool_registry import ToolResult

        return ToolResult(
            tool_name=self._spec.name,
            content=json.dumps({"echo": params}),
            success=True,
        )


def _make_dummy(name: str = "dummy", **kwargs) -> DummyTool:
    return DummyTool(name=name, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Singleton pattern
# ═══════════════════════════════════════════════════════════════════════════


class TestSingleton:
    def test_singleton_pattern(self):
        """UnifiedToolRegistry() always returns the same instance."""
        r1 = UnifiedToolRegistry()
        r2 = UnifiedToolRegistry()
        assert r1 is r2

    def test_get_registry_returns_singleton(self):
        """get_registry() returns the same object as direct construction."""
        r1 = get_registry()
        r2 = UnifiedToolRegistry()
        assert r1 is r2

    def test_reset_creates_new_instance(self):
        """reset() causes the next call to create a fresh instance."""
        r1 = UnifiedToolRegistry()
        r1.register(_make_dummy("a"))
        assert len(r1) == 1
        UnifiedToolRegistry.reset()
        r2 = UnifiedToolRegistry()
        assert len(r2) == 0
        assert r1 is not r2


# ═══════════════════════════════════════════════════════════════════════════
# 2. Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistration:
    def test_register_tool(self):
        """A tool can be registered and then found."""
        reg = get_registry()
        tool = _make_dummy("calc")
        reg.register(tool)
        assert "calc" in reg
        assert len(reg) == 1

    def test_register_duplicate_skips(self):
        """Registering the same name twice does not raise — logs warning and skips."""
        reg = get_registry()
        reg.register(_make_dummy("calc"))
        reg.register(_make_dummy("calc"))  # should not raise
        assert len(reg) == 1

    def test_unregister(self):
        """unregister() removes a tool and returns True; returns False for missing."""
        reg = get_registry()
        reg.register(_make_dummy("x"))
        assert reg.unregister("x") is True
        assert reg.unregister("x") is False
        assert len(reg) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. Discovery
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscovery:
    def test_discover_all_tools(self):
        """discover() returns specs for all registered tools."""
        reg = get_registry()
        reg.register(_make_dummy("a"))
        reg.register(_make_dummy("b"))
        reg.register(_make_dummy("c"))
        specs = reg.discover()
        assert len(specs) == 3
        names = {s.name for s in specs}
        assert names == {"a", "b", "c"}

    def test_discover_empty_registry(self):
        """discover() returns empty list when nothing is registered."""
        reg = get_registry()
        assert reg.discover() == []

    def test_find_by_name(self):
        """find() returns the ToolSpec for an exact name match."""
        reg = get_registry()
        reg.register(_make_dummy("calculator", description="Math tool"))
        spec = reg.find("calculator")
        assert spec is not None
        assert spec.name == "calculator"
        assert spec.description == "Math tool"

    def test_find_nonexistent_returns_none(self):
        """find() returns None when the name does not exist."""
        reg = get_registry()
        assert reg.find("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. Search
# ═══════════════════════════════════════════════════════════════════════════


class TestSearch:
    def test_search_by_query(self):
        """search(query=...) matches against name and description."""
        reg = get_registry()
        reg.register(_make_dummy("tavily_search", description="Search the web"))
        reg.register(_make_dummy("calculator", description="Math operations"))
        reg.register(_make_dummy("web_scraper", description="Scrape web pages"))

        results = reg.search(query="web")
        assert len(results) == 2
        names = {s.name for s in results}
        assert names == {"tavily_search", "web_scraper"}

    def test_search_by_tags(self):
        """search(tags=...) filters by tag overlap."""
        reg = get_registry()
        reg.register(_make_dummy("a", tags=["email", "outbound"]))
        reg.register(_make_dummy("b", tags=["browser", "scraping"]))
        reg.register(_make_dummy("c", tags=["email", "marketing"]))

        results = reg.search(tags=["email"])
        assert len(results) == 2
        names = {s.name for s in results}
        assert names == {"a", "c"}

    def test_search_by_tool_type(self):
        """search(tool_type=...) filters by backend type."""
        reg = get_registry()
        reg.register(_make_dummy("x", tool_type=ToolType.NATIVE))
        reg.register(_make_dummy("y", tool_type=ToolType.SKILL))
        reg.register(_make_dummy("z", tool_type=ToolType.HANDLER))

        results = reg.search(tool_type=ToolType.SKILL)
        assert len(results) == 1
        assert results[0].name == "y"

    def test_search_by_category(self):
        """search(category=...) filters by category string."""
        reg = get_registry()
        reg.register(_make_dummy("a", category="math"))
        reg.register(_make_dummy("b", category="pipeline"))
        reg.register(_make_dummy("c", category="math"))

        results = reg.search(category="math")
        assert len(results) == 2

    def test_search_combined_filters(self):
        """search() combines query + tags filters."""
        reg = get_registry()
        reg.register(_make_dummy("email_send", tags=["email"], description="Send email"))
        reg.register(
            _make_dummy("email_read", tags=["email"], description="Read email inbox")
        )
        reg.register(
            _make_dummy("web_search", tags=["search"], description="Search the web")
        )

        results = reg.search(query="send", tags=["email"])
        assert len(results) == 1
        assert results[0].name == "email_send"

    def test_search_no_results(self):
        """search() returns empty list when nothing matches."""
        reg = get_registry()
        reg.register(_make_dummy("calc"))
        assert reg.search(query="nonexistent") == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. Available (env filtering)
# ═══════════════════════════════════════════════════════════════════════════


class TestAvailable:
    def test_available_filters_by_env(self):
        """available() only returns tools whose env vars are satisfied."""
        reg = get_registry()
        # Tool with no env requirement — always available
        reg.register(_make_dummy("free_tool"))
        # Tool requiring an env var that is NOT set
        reg.register(
            _make_dummy("paid_tool", requires_env=["NONEXISTENT_KEY_12345"])
        )
        # Tool requiring an env var that IS set
        os.environ["TEST_TOOL_REGISTRY_KEY"] = "present"
        try:
            reg.register(
                _make_dummy("has_key_tool", requires_env=["TEST_TOOL_REGISTRY_KEY"])
            )
            avail = reg.available()
            names = {s.name for s in avail}
            assert "free_tool" in names
            assert "has_key_tool" in names
            assert "paid_tool" not in names
        finally:
            del os.environ["TEST_TOOL_REGISTRY_KEY"]


# ═══════════════════════════════════════════════════════════════════════════
# 6. ToolSpec helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestToolSpec:
    def test_env_satisfied_empty(self):
        """env_satisfied() returns True when requires_env is empty."""
        spec = ToolSpec(name="t", description="d")
        assert spec.env_satisfied() is True

    def test_env_satisfied_missing(self):
        """env_satisfied() returns False when a required var is missing."""
        spec = ToolSpec(name="t", description="d", requires_env=["MISSING_VAR_XYZ"])
        assert spec.env_satisfied() is False

    def test_to_openai_function(self):
        """to_openai_function() produces the expected shape."""
        spec = ToolSpec(
            name="calc",
            description="Calculate",
            parameters={"type": "object", "properties": {"x": {"type": "number"}}},
        )
        result = spec.to_openai_function()
        assert result["type"] == "function"
        assert result["function"]["name"] == "calc"
        assert "x" in result["function"]["parameters"]["properties"]

    def test_to_mcp_tool(self):
        """to_mcp_tool() produces the expected shape."""
        spec = ToolSpec(name="t", description="d", requires_confirmation=True)
        result = spec.to_mcp_tool()
        assert result["name"] == "t"
        assert result["annotations"]["destructiveHint"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 7. @register_tool decorator
# ═══════════════════════════════════════════════════════════════════════════


class TestRegisterToolDecorator:
    def test_register_tool_decorator(self):
        """@register_tool creates a pending HandlerToolAdapter."""
        _PENDING_REGISTRATIONS.clear()

        @register_tool(
            name="my_tool",
            description="Does something",
            tags=["utility"],
        )
        def my_tool(query: str, limit: int = 10) -> dict:
            return {"results": []}

        assert len(_PENDING_REGISTRATIONS) == 1
        adapter = _PENDING_REGISTRATIONS[0]
        assert adapter.spec.name == "my_tool"
        assert adapter.spec.description == "Does something"
        assert "utility" in adapter.spec.tags

    def test_decorator_generates_schema(self):
        """@register_tool auto-generates JSON Schema from type hints."""
        _PENDING_REGISTRATIONS.clear()

        @register_tool(name="schema_test")
        def schema_test(query: str, limit: int = 5, verbose: bool = False) -> list:
            return []

        adapter = _PENDING_REGISTRATIONS[0]
        schema = adapter.spec.parameters
        assert schema["type"] == "object"
        props = schema["properties"]
        assert props["query"]["type"] == "string"
        assert props["limit"]["type"] == "integer"
        assert props["limit"]["default"] == 5
        assert props["verbose"]["type"] == "boolean"
        assert "query" in schema["required"]
        # limit and verbose have defaults, so not required
        assert "limit" not in schema["required"]
        assert "verbose" not in schema["required"]

    def test_decorator_uses_docstring_as_fallback(self):
        """If no description given, uses function docstring first line."""
        _PENDING_REGISTRATIONS.clear()

        @register_tool(name="doc_tool")
        def doc_tool(x: int) -> int:
            """Compute the square of x."""
            return x * x

        adapter = _PENDING_REGISTRATIONS[0]
        assert adapter.spec.description == "Compute the square of x."

    def test_decorated_function_still_callable(self):
        """The decorated function remains callable."""
        @register_tool(name="callable_test")
        def callable_test(x: int) -> int:
            return x + 1

        assert callable_test(5) == 6


# ═══════════════════════════════════════════════════════════════════════════
# 8. _fn_to_json_schema
# ═══════════════════════════════════════════════════════════════════════════


class TestFnToJsonSchema:
    def test_basic_types(self):
        def fn(a: str, b: int, c: float, d: bool) -> None:
            pass

        schema = _fn_to_json_schema(fn)
        assert schema["properties"]["a"]["type"] == "string"
        assert schema["properties"]["b"]["type"] == "integer"
        assert schema["properties"]["c"]["type"] == "number"
        assert schema["properties"]["d"]["type"] == "boolean"
        assert set(schema["required"]) == {"a", "b", "c", "d"}

    def test_defaults_excluded_from_required(self):
        def fn(a: str, b: int = 10) -> None:
            pass

        schema = _fn_to_json_schema(fn)
        assert "a" in schema["required"]
        assert "b" not in schema["required"]
        assert schema["properties"]["b"]["default"] == 10


# ═══════════════════════════════════════════════════════════════════════════
# 9. SkillToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class TestSkillToolAdapter:
    def test_skill_tool_adapter(self, tmp_path: Path):
        """SkillToolAdapter parses SKILL.md and creates correct spec."""
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            textwrap.dedent("""\
            # My Skill

            This skill does amazing things.

            ## Parameters
            - `query` (string): The search query
            - `limit` (integer): Max results to return
            """)
        )

        adapter = SkillToolAdapter(skill_md, "my_skill")
        spec = adapter.spec

        assert spec.name == "my_skill"
        assert spec.tool_type == ToolType.SKILL
        assert "amazing" in spec.description
        assert spec.parameters["properties"]["query"]["type"] == "string"
        assert spec.parameters["properties"]["limit"]["type"] == "integer"
        assert "skill" in spec.tags

    def test_skill_adapter_missing_file(self, tmp_path: Path):
        """SkillToolAdapter handles missing file gracefully."""
        adapter = SkillToolAdapter(tmp_path / "nonexistent" / "SKILL.md", "missing")
        spec = adapter.spec
        assert spec.name == "missing"
        assert spec.tool_type == ToolType.SKILL

    def test_skill_adapter_registers_in_registry(self, tmp_path: Path):
        """SkillToolAdapter can be registered in the registry."""
        skill_dir = tmp_path / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\n\nDoes things.\n\n## Steps\n- Do it\n")

        reg = get_registry()
        adapter = SkillToolAdapter(skill_dir / "SKILL.md", "test_skill")
        reg.register(adapter)

        assert "test_skill" in reg
        assert reg.find("test_skill").tool_type == ToolType.SKILL


# ═══════════════════════════════════════════════════════════════════════════
# 10. HandlerToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class TestHandlerToolAdapter:
    def test_handler_tool_adapter_sync(self):
        """HandlerToolAdapter wraps a sync function correctly."""

        def my_handler(x: int, y: int) -> dict:
            return {"sum": x + y}

        adapter = HandlerToolAdapter(
            handler_fn=my_handler,
            name="adder",
            description="Add two numbers",
            category="math",
            tags=["math", "utility"],
        )

        assert adapter.spec.name == "adder"
        assert adapter.spec.tool_type == ToolType.HANDLER
        assert adapter.spec.description == "Add two numbers"

        result = adapter.execute(x=3, y=4)
        assert result.success is True
        data = json.loads(result.content)
        assert data["sum"] == 7

    def test_handler_tool_adapter_async(self):
        """HandlerToolAdapter wraps an async function correctly."""

        async def async_handler(msg: str) -> str:
            return f"echo: {msg}"

        adapter = HandlerToolAdapter(
            handler_fn=async_handler,
            name="echo",
            description="Echo a message",
        )

        assert adapter.spec.name == "echo"
        result = adapter.execute(msg="hello")
        assert result.success is True
        assert "echo: hello" in result.content

    def test_handler_tool_adapter_error(self):
        """HandlerToolAdapter returns error ToolResult on exception."""

        def bad_handler() -> None:
            raise ValueError("boom")

        adapter = HandlerToolAdapter(handler_fn=bad_handler, name="bad")
        result = adapter.execute()
        assert result.success is False
        assert "boom" in result.content

    def test_handler_uses_docstring(self):
        """HandlerToolAdapter uses function docstring as description fallback."""

        def documented():
            """This handler is documented."""
            pass

        adapter = HandlerToolAdapter(handler_fn=documented, name="doc")
        assert adapter.spec.description == "This handler is documented."


# ═══════════════════════════════════════════════════════════════════════════
# 11. MCPRemoteToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPRemoteToolAdapter:
    def test_mcp_remote_tool_adapter(self):
        """MCPRemoteToolAdapter creates correct spec from tool_def."""
        mock_client = MagicMock()
        tool_def = {
            "name": "search",
            "description": "Search documents",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
        adapter = MCPRemoteToolAdapter(mock_client, tool_def, "qdrant")

        spec = adapter.spec
        assert spec.name == "qdrant__search"
        assert spec.tool_type == ToolType.MCP_REMOTE
        assert "mcp" in spec.tags
        assert spec.source == "mcp:qdrant"

    def test_mcp_execute_success(self):
        """MCPRemoteToolAdapter dispatches to client.call_tool."""
        mock_client = MagicMock()
        mock_client.call_tool.return_value = {
            "content": [{"text": "result data"}],
            "isError": False,
        }

        adapter = MCPRemoteToolAdapter(
            mock_client,
            {"name": "search", "description": "Search"},
            "test_server",
        )
        result = adapter.execute(query="hello")

        assert result.success is True
        assert "result data" in result.content
        mock_client.call_tool.assert_called_once_with("search", {"query": "hello"})

    def test_mcp_execute_error(self):
        """MCPRemoteToolAdapter handles isError=True."""
        mock_client = MagicMock()
        mock_client.call_tool.return_value = {
            "content": [{"text": "error occurred"}],
            "isError": True,
        }

        adapter = MCPRemoteToolAdapter(
            mock_client,
            {"name": "broken", "description": "Broken tool"},
            "srv",
        )
        result = adapter.execute()
        assert result.success is False


# ═══════════════════════════════════════════════════════════════════════════
# 12. Execution dispatch
# ═══════════════════════════════════════════════════════════════════════════


class TestExecution:
    def test_execute_dispatches_correctly(self):
        """execute() calls the tool's execute method and returns result."""
        reg = get_registry()
        reg.register(_make_dummy("echo"))

        result = asyncio.get_event_loop().run_until_complete(
            reg.execute("echo", {"msg": "hi"})
        )
        assert result.success is True
        data = json.loads(result.content)
        assert data["echo"]["msg"] == "hi"

    def test_execute_unknown_tool(self):
        """execute() returns failure for unknown tool name."""
        reg = get_registry()
        result = asyncio.get_event_loop().run_until_complete(
            reg.execute("nonexistent", {})
        )
        assert result.success is False
        assert "Unknown tool" in result.content

    def test_execute_measures_latency(self):
        """execute() records latency_seconds on the result."""
        reg = get_registry()
        reg.register(_make_dummy("fast"))

        result = asyncio.get_event_loop().run_until_complete(
            reg.execute("fast", {})
        )
        assert result.latency_seconds >= 0


# ═══════════════════════════════════════════════════════════════════════════
# 13. Budget gate middleware
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetGate:
    def test_execute_budget_gate_blocks(self):
        """Budget middleware rejects when budget is exceeded."""
        reg = get_registry()

        # Tool with estimated cost
        reg.register(_make_dummy("expensive", cost=10.0))

        # Add a middleware that always rejects
        async def always_reject(tool, params, agent_id, context):
            from shared.tool_registry import ToolResult

            if tool.spec.estimated_cost_per_call > 0:
                return ToolResult(
                    tool_name=tool.spec.name,
                    content="Budget exceeded",
                    success=False,
                    metadata={"rejection_reason": "budget_exceeded"},
                )
            return None

        reg.add_middleware(always_reject)

        result = asyncio.get_event_loop().run_until_complete(
            reg.execute("expensive", {})
        )
        assert result.success is False
        assert "Budget exceeded" in result.content

    def test_budget_gate_allows_free_tools(self):
        """budget_gate_middleware allows tools with cost=0."""
        tool = _make_dummy("free", cost=0.0)
        result = asyncio.get_event_loop().run_until_complete(
            budget_gate_middleware(tool, {}, "agent1", {})
        )
        assert result is None  # None means "allowed"

    def test_budget_gate_allows_when_budget_available(self):
        """budget_gate_middleware allows when budget check passes."""
        tool = _make_dummy("cheap", cost=0.01)

        with patch("tools.budget_guard.BudgetGuard") as MockGuard:
            instance = MockGuard.return_value
            instance.can_spend = AsyncMock(
                return_value={"allowed": True, "remaining": 100.0}
            )
            result = asyncio.get_event_loop().run_until_complete(
                budget_gate_middleware(tool, {}, "agent1", {})
            )
            assert result is None  # allowed

    def test_budget_gate_rejects_when_exceeded(self):
        """budget_gate_middleware rejects when budget is exceeded."""
        tool = _make_dummy("pricey", cost=50.0)

        with patch("tools.budget_guard.BudgetGuard") as MockGuard:
            instance = MockGuard.return_value
            instance.can_spend = AsyncMock(
                return_value={
                    "allowed": False,
                    "exceeded": True,
                    "reason": "Over budget",
                    "remaining": 0.0,
                }
            )
            result = asyncio.get_event_loop().run_until_complete(
                budget_gate_middleware(tool, {}, "agent1", {})
            )
            assert result is not None
            assert result.success is False
            assert "Budget exceeded" in result.content


# ═══════════════════════════════════════════════════════════════════════════
# 14. Post-execution logging middleware
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetLog:
    def test_execute_logs_to_activity_log(self):
        """Post-execution middleware is invoked after successful execute."""
        reg = get_registry()
        reg.register(_make_dummy("logged_tool"))

        log_calls = []

        async def mock_post_mw(tool, result, agent_id):
            log_calls.append(
                {"tool": tool.spec.name, "success": result.success, "agent": agent_id}
            )

        reg.add_post_middleware(mock_post_mw)

        asyncio.get_event_loop().run_until_complete(
            reg.execute("logged_tool", {}, agent_id="titan")
        )

        assert len(log_calls) == 1
        assert log_calls[0]["tool"] == "logged_tool"
        assert log_calls[0]["agent"] == "titan"
        assert log_calls[0]["success"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 15. Auto-scan
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoScan:
    def test_scan_and_register(self):
        """auto_scan_all with mocked imports registers tools."""
        from shared.tool_registry import auto_scan_all, _scan_decorated_tools

        # Put a pending registration in the queue
        @register_tool(name="scan_test", description="From scan")
        def scan_test_fn(x: int) -> int:
            return x

        # Run scan with skip flags to avoid real imports
        auto_scan_all(skip_oj=True, skip_skills=True, skip_handlers=True)

        reg = get_registry()
        assert "scan_test" in reg
        spec = reg.find("scan_test")
        assert spec.description == "From scan"

    def test_scan_skill_files(self, tmp_path: Path):
        """_scan_skill_files discovers SKILL.md files in a directory tree."""
        from shared.tool_registry import _scan_skill_files

        # Create a skill directory structure
        skill_a = tmp_path / "skill_a"
        skill_a.mkdir()
        (skill_a / "SKILL.md").write_text("# Skill A\n\nDoes A.\n\n## Steps\n- step 1\n")

        skill_b = tmp_path / "nested" / "skill_b"
        skill_b.mkdir(parents=True)
        (skill_b / "SKILL.md").write_text("# Skill B\n\nDoes B.\n\n## Steps\n- step 1\n")

        reg = get_registry()
        _scan_skill_files(reg, [tmp_path])

        assert "skill_a" in reg
        assert "skill_b" in reg

    def test_scan_handler_dicts(self):
        """_scan_handler_dicts wraps TASK_HANDLERS entries."""
        from shared.tool_registry import _scan_handler_dicts
        import types

        # Create a fake module with TASK_HANDLERS
        fake_mod = types.ModuleType("fake_daemon")
        fake_mod.TASK_HANDLERS = {
            "do_thing": lambda ctx: "done",
            "do_other": lambda ctx: "also done",
        }

        import sys

        sys.modules["fake.daemon"] = fake_mod

        reg = get_registry()

        # Temporarily add our fake module to the scan list
        from shared import tool_registry

        original = tool_registry.HANDLER_MODULES
        tool_registry.HANDLER_MODULES = [("fake.daemon", "TASK_HANDLERS")]
        try:
            _scan_handler_dicts(reg)
            assert "fake:do_thing" in reg
            assert "fake:do_other" in reg
        finally:
            tool_registry.HANDLER_MODULES = original
            del sys.modules["fake.daemon"]


# ═══════════════════════════════════════════════════════════════════════════
# 16. Middleware pipeline ordering
# ═══════════════════════════════════════════════════════════════════════════


class TestMiddlewarePipeline:
    def test_middleware_short_circuits(self):
        """First middleware that returns a ToolResult stops the chain."""
        reg = get_registry()
        reg.register(_make_dummy("guarded"))

        call_order = []

        async def mw1(tool, params, agent_id, ctx):
            call_order.append("mw1")
            return None  # pass through

        async def mw2(tool, params, agent_id, ctx):
            call_order.append("mw2")
            from shared.tool_registry import ToolResult

            return ToolResult(tool_name="guarded", content="blocked", success=False)

        async def mw3(tool, params, agent_id, ctx):
            call_order.append("mw3")  # should never be reached
            return None

        reg.add_middleware(mw1)
        reg.add_middleware(mw2)
        reg.add_middleware(mw3)

        result = asyncio.get_event_loop().run_until_complete(
            reg.execute("guarded", {})
        )
        assert result.success is False
        assert call_order == ["mw1", "mw2"]  # mw3 never called


# ═══════════════════════════════════════════════════════════════════════════
# 17. OpenAI tools format
# ═══════════════════════════════════════════════════════════════════════════


class TestOpenAIFormat:
    def test_get_openai_tools(self):
        """get_openai_tools() returns all tools in OpenAI format."""
        reg = get_registry()
        reg.register(_make_dummy("a", description="Tool A"))
        reg.register(_make_dummy("b", description="Tool B"))

        tools = reg.get_openai_tools()
        assert len(tools) == 2
        assert all(t["type"] == "function" for t in tools)
        names = {t["function"]["name"] for t in tools}
        assert names == {"a", "b"}
