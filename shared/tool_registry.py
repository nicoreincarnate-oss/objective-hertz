"""Unified ToolRegistry — Single registry for all Perseus tool types.

Consolidates four previously disconnected tool systems:
1. OJ BaseTool subclasses (openjarvis/tools/_stubs.py)
2. SKILL.md procedural skills (shared/skill_loader.py)
3. TASK_HANDLERS bare Python functions (daemon modules)
4. MCP remote tools (JSON-RPC over transport)

Design doc: .planning/phases/phase-0b/TOOLSPEC-DESIGN.md
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, get_type_hints

logger = logging.getLogger("perseus.tool_registry")

# ---------------------------------------------------------------------------
# Import guards — OJ types may not be available in all environments
# ---------------------------------------------------------------------------

try:
    from openjarvis.core.types import ToolCall, ToolResult
except ImportError:

    @dataclass
    class ToolCall:  # type: ignore[no-redef]
        """Fallback ToolCall when OJ is not available."""

        id: str = ""
        name: str = ""
        arguments: str = ""

    @dataclass
    class ToolResult:  # type: ignore[no-redef]
        """Fallback ToolResult when OJ is not available."""

        tool_name: str = ""
        content: str = ""
        success: bool = True
        usage: dict[str, Any] = field(default_factory=dict)
        cost_usd: float = 0.0
        latency_seconds: float = 0.0
        metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# ToolType enum
# ═══════════════════════════════════════════════════════════════════════════


class ToolType(str, Enum):
    """Identifies the execution backend for a tool."""

    NATIVE = "native"  # OJ BaseTool subclass
    SKILL = "skill"  # SKILL.md procedural skill
    HANDLER = "handler"  # Bare Python async/sync function
    MCP_REMOTE = "mcp_remote"  # Remote MCP server tool


# ═══════════════════════════════════════════════════════════════════════════
# Extended ToolSpec dataclass
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ToolSpec:
    """Declarative description of a tool's interface and characteristics.

    Backwards-compatible with existing OJ ToolSpec — all new fields have
    defaults so existing ``@ToolRegistry.register()`` code works unchanged.
    """

    # -- Identity (existing OJ fields) ------------------------------------
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    category: str = ""

    # -- Execution characteristics (existing OJ fields) -------------------
    cost_estimate: float = 0.0
    latency_estimate: float = 0.0
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    required_capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- NEW: Unified extensions ------------------------------------------

    tool_type: ToolType = ToolType.NATIVE
    """Identifies which execution backend handles this tool."""

    output_schema: dict[str, Any] = field(default_factory=dict)
    """JSON Schema describing the tool's return value structure."""

    tags: list[str] = field(default_factory=list)
    """Free-form tags for fuzzy search and discovery."""

    source: str = ""
    """Provenance string identifying who registered this tool."""

    requires_env: list[str] = field(default_factory=list)
    """Environment variables this tool needs to function."""

    estimated_cost_per_call: float = 0.0
    """Estimated USD cost per invocation for budget gating."""

    # -- Computed helpers -------------------------------------------------

    def env_satisfied(self) -> bool:
        """Check whether all required env vars are set."""
        return all(os.environ.get(var) for var in self.requires_env)

    def to_openai_function(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        """Convert to MCP tools/list format."""
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters
            or {"type": "object", "properties": {}},
        }
        annotations: dict[str, Any] = {}
        if self.requires_confirmation:
            annotations["destructiveHint"] = True
        if self.category in ("read", "search", "math", "retrieval"):
            annotations["readOnlyHint"] = True
        if annotations:
            result["annotations"] = annotations
        return result


# ═══════════════════════════════════════════════════════════════════════════
# BaseTool ABC (local version — does NOT shadow OJ's BaseTool)
# ═══════════════════════════════════════════════════════════════════════════


class UnifiedBaseTool(ABC):
    """Base class for tool implementations in the unified registry.

    Subclasses must provide a ``spec`` property and ``execute()`` method.
    This is separate from OJ's ``BaseTool`` to avoid import coupling,
    but the adapters bridge OJ tools into this interface.
    """

    tool_id: str = ""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""

    @abstractmethod
    def execute(self, **params: Any) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_openai_function(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        return self.spec.to_openai_function()


# ═══════════════════════════════════════════════════════════════════════════
# Adapter: SkillToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class SkillToolAdapter(UnifiedBaseTool):
    """Adapt a SKILL.md procedural skill into a UnifiedBaseTool.

    Parses the SKILL.md to extract name, description, and parameters.
    Execution delegates to the skill runner (shared/skill_loader.py).
    """

    def __init__(self, skill_path: Path | str, skill_name: str = "") -> None:
        self._path = Path(skill_path)
        self._name = skill_name or self._path.parent.name
        try:
            self._content = self._path.read_text()
        except Exception:
            self._content = ""
        self._parsed = self._parse_skill_md()
        self.tool_id = f"skill:{self._name}"

    def _parse_skill_md(self) -> dict[str, Any]:
        """Extract structured data from SKILL.md content."""
        content = self._content
        parsed: dict[str, Any] = {
            "name": self._name,
            "description": f"Skill: {self._name}",
            "params": {},
        }

        if not content:
            return parsed

        # Extract description (first paragraph after heading)
        desc_match = re.search(r"^#\s+.*?\n\n(.+?)(?:\n\n|\n##)", content, re.S)
        if desc_match:
            parsed["description"] = desc_match.group(1).strip()

        # Extract parameters section if present
        params_match = re.search(
            r"##\s*(?:Parameters|Inputs)\s*\n(.*?)(?:\n##|\Z)", content, re.S
        )
        if params_match:
            for line in params_match.group(1).strip().split("\n"):
                param_match = re.match(
                    r"-\s+`(\w+)`\s*(?:\((\w+)\))?\s*[:-]\s*(.*)", line
                )
                if param_match:
                    pname, ptype, pdesc = param_match.groups()
                    parsed["params"][pname] = {
                        "type": ptype or "string",
                        "description": pdesc.strip(),
                    }

        return parsed

    @property
    def spec(self) -> ToolSpec:
        params = self._parsed.get("params", {})
        return ToolSpec(
            name=self._name,
            description=self._parsed.get("description", ""),
            parameters={
                "type": "object",
                "properties": params,
                "required": list(params.keys()),
            }
            if params
            else {"type": "object", "properties": {}},
            category="skill",
            tool_type=ToolType.SKILL,
            source=f"skill:{self._path}",
            tags=["skill", self._name],
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        """Execute the skill by delegating to the skill runner."""
        try:
            from shared.skill_loader import execute_skill

            # execute_skill is async — run it
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    execute_skill(
                        self._name,
                        json.dumps(params) if params else "Execute skill",
                        context=params,
                    )
                )
            finally:
                loop.close()
            return ToolResult(
                tool_name=self._name,
                content=str(result),
                success=True,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self._name,
                content=f"Skill execution error: {exc}",
                success=False,
            )


# ═══════════════════════════════════════════════════════════════════════════
# Adapter: HandlerToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class HandlerToolAdapter(UnifiedBaseTool):
    """Adapt a bare Python function into a UnifiedBaseTool.

    Wraps functions from TASK_HANDLERS dicts that currently lack
    schema validation, timeout, RBAC, and observability.
    """

    def __init__(
        self,
        handler_fn: Callable,
        name: str,
        schema: dict[str, Any] | None = None,
        description: str = "",
        category: str = "handler",
        cost_estimate: float = 0.0,
        required_capabilities: list[str] | None = None,
        source: str = "",
        tags: list[str] | None = None,
        requires_env: list[str] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._fn = handler_fn
        self._is_async = asyncio.iscoroutinefunction(handler_fn)
        self._name = name
        self._schema = schema or {"type": "object", "properties": {}}
        self._description = (
            description or (handler_fn.__doc__ or "").strip() or name
        )
        self._category = category
        self._cost_estimate = cost_estimate
        self._required_capabilities = required_capabilities or []
        self._source = source or f"handler:{handler_fn.__module__}.{handler_fn.__qualname__}"
        self._tags = tags or ["handler"]
        self._requires_env = requires_env or []
        self._timeout = timeout_seconds
        self.tool_id = f"handler:{name}"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description=self._description,
            parameters=self._schema,
            category=self._category,
            tool_type=ToolType.HANDLER,
            cost_estimate=self._cost_estimate,
            estimated_cost_per_call=self._cost_estimate,
            required_capabilities=self._required_capabilities,
            source=self._source,
            tags=self._tags,
            requires_env=self._requires_env,
            timeout_seconds=self._timeout,
        )

    def execute(self, **params: Any) -> ToolResult:
        """Execute the handler function (sync or async)."""
        try:
            if self._is_async:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(self._fn(**params))
                finally:
                    loop.close()
            else:
                result = self._fn(**params)

            content = (
                json.dumps(result)
                if isinstance(result, (dict, list))
                else str(result)
            )
            return ToolResult(
                tool_name=self._name,
                content=content,
                success=True,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self._name,
                content=f"Handler error: {exc}",
                success=False,
            )


# ═══════════════════════════════════════════════════════════════════════════
# Adapter: MCPRemoteToolAdapter
# ═══════════════════════════════════════════════════════════════════════════


class MCPRemoteToolAdapter(UnifiedBaseTool):
    """Adapt a remote MCP server's tool into a UnifiedBaseTool.

    Uses an MCP client to call tools/call on the remote server.
    """

    def __init__(
        self,
        mcp_client: Any,
        tool_def: dict[str, Any],
        server_name: str = "unknown",
    ) -> None:
        self._client = mcp_client
        self._tool_def = tool_def
        self._server_name = server_name
        self._name = tool_def.get("name", "unknown")
        self.tool_id = f"mcp:{server_name}:{self._name}"

    @property
    def spec(self) -> ToolSpec:
        annotations = self._tool_def.get("annotations", {})
        return ToolSpec(
            name=f"{self._server_name}__{self._name}",
            description=self._tool_def.get("description", ""),
            parameters=self._tool_def.get(
                "inputSchema", {"type": "object", "properties": {}}
            ),
            category="mcp_remote",
            tool_type=ToolType.MCP_REMOTE,
            source=f"mcp:{self._server_name}",
            tags=["mcp", self._server_name],
            requires_confirmation=annotations.get("destructiveHint", False),
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        """Call the remote MCP tool via the client."""
        try:
            response = self._client.call_tool(self._name, params)

            is_error = response.get("isError", False)
            content_parts = response.get("content", [])
            text = "\n".join(
                p.get("text", str(p))
                for p in content_parts
                if isinstance(p, dict)
            )

            return ToolResult(
                tool_name=self._name,
                content=text,
                success=not is_error,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self._name,
                content=f"MCP remote error: {exc}",
                success=False,
            )


# ═══════════════════════════════════════════════════════════════════════════
# @register_tool decorator
# ═══════════════════════════════════════════════════════════════════════════

# Pending registrations — flushed during auto-scan
_PENDING_REGISTRATIONS: list[UnifiedBaseTool] = []

# Python type -> JSON Schema type map
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _fn_to_json_schema(fn: Callable) -> dict[str, Any]:
    """Convert function signature + type hints to JSON Schema."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        hint = hints.get(pname, str)
        # Handle Optional/Union types — extract the base type
        origin = getattr(hint, "__origin__", None)
        if origin is not None:
            args = getattr(hint, "__args__", ())
            # For list[X], dict[X, Y], etc.
            if origin is list:
                hint = list
            elif origin is dict:
                hint = dict
            else:
                # Try first non-None arg for Optional[X]
                non_none = [a for a in args if a is not type(None)]
                hint = non_none[0] if non_none else str

        json_type = _TYPE_MAP.get(hint, "string")
        prop: dict[str, Any] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(pname)
        elif param.default is not None:
            prop["default"] = param.default

        properties[pname] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def register_tool(
    name: str | None = None,
    description: str = "",
    category: str = "",
    tags: list[str] | None = None,
    cost_estimate: float = 0.0,
    requires_env: list[str] | None = None,
    requires_confirmation: bool = False,
    timeout_seconds: float = 30.0,
    required_capabilities: list[str] | None = None,
) -> Callable:
    """Decorator that registers a Python function as a tool.

    Usage::

        @register_tool(name="my_tool", description="Does X", tags=["utility"])
        def my_tool(query: str, limit: int = 10) -> dict:
            '''Detailed docstring used as fallback description.'''
            return {"results": [...]}

    Type hints on the function signature are converted to JSON Schema
    parameters automatically.
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tool_desc = (
            description
            or (fn.__doc__ or "").strip().split("\n")[0]
            or tool_name
        )

        schema = _fn_to_json_schema(fn)

        adapter = HandlerToolAdapter(
            handler_fn=fn,
            name=tool_name,
            schema=schema,
            description=tool_desc,
            category=category,
            cost_estimate=cost_estimate,
            required_capabilities=required_capabilities or [],
            source=f"decorator:{fn.__module__}.{fn.__qualname__}",
            tags=tags or [],
            requires_env=requires_env or [],
            timeout_seconds=timeout_seconds,
        )

        # Defer registration to startup scan
        _PENDING_REGISTRATIONS.append(adapter)

        # Mark function so scanner can find it
        fn._tool_adapter = adapter  # type: ignore[attr-defined]
        return fn

    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# UnifiedToolRegistry — Singleton
# ═══════════════════════════════════════════════════════════════════════════


class UnifiedToolRegistry:
    """Singleton registry for all tool types in the Perseus system.

    Provides registration, discovery, search, env filtering, and
    execution dispatch with middleware pipeline.
    """

    _instance: UnifiedToolRegistry | None = None
    _lock: asyncio.Lock | None = None

    def __new__(cls) -> UnifiedToolRegistry:
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._tools: dict[str, UnifiedBaseTool] = {}
            inst._middleware: list[Callable] = []
            inst._post_middleware: list[Callable] = []
            cls._instance = inst
        return cls._instance

    # -- Registration -----------------------------------------------------

    def register(self, tool: UnifiedBaseTool) -> None:
        """Register a tool instance. Skips duplicates with a warning."""
        name = tool.spec.name
        if name in self._tools:
            logger.warning("Tool '%s' already registered, skipping duplicate", name)
            return
        self._tools[name] = tool
        logger.debug(
            "Registered tool: %s (type=%s, source=%s)",
            name,
            tool.spec.tool_type.value,
            tool.spec.source,
        )

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True if it existed."""
        return self._tools.pop(name, None) is not None

    # -- Discovery --------------------------------------------------------

    def discover(self) -> list[ToolSpec]:
        """Return specs for ALL registered tools (including unavailable)."""
        return [t.spec for t in self._tools.values()]

    def find(self, name: str) -> ToolSpec | None:
        """Look up a tool by exact name."""
        tool = self._tools.get(name)
        return tool.spec if tool else None

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        tool_type: ToolType | None = None,
        category: str = "",
    ) -> list[ToolSpec]:
        """Fuzzy search tools by name/description/tags.

        Parameters
        ----------
        query : str
            Substring match against name and description (case-insensitive).
        tags : list[str] | None
            If provided, tool must have at least one matching tag.
        tool_type : ToolType | None
            Filter to specific backend type.
        category : str
            Filter to specific category.
        """
        results: list[ToolSpec] = []
        query_lower = query.lower() if query else ""

        for tool in self._tools.values():
            s = tool.spec

            # Type filter
            if tool_type is not None and s.tool_type != tool_type:
                continue

            # Category filter
            if category and s.category != category:
                continue

            # Tag filter (any match)
            if tags and not set(tags) & set(s.tags):
                continue

            # Query match (name or description or tags)
            if query_lower:
                name_match = query_lower in s.name.lower()
                desc_match = query_lower in s.description.lower()
                tag_match = any(query_lower in t.lower() for t in s.tags)
                if not (name_match or desc_match or tag_match):
                    continue

            results.append(s)

        return results

    def available(self) -> list[ToolSpec]:
        """Return specs for tools whose env requirements are satisfied."""
        return [t.spec for t in self._tools.values() if t.spec.env_satisfied()]

    def available_for_agent(
        self,
        agent_id: str,
        capability_policy: Any = None,
    ) -> list[ToolSpec]:
        """Return tools available to a specific agent (RBAC-filtered).

        Combines env_satisfied() check with capability policy check.
        """
        results: list[ToolSpec] = []
        for tool in self._tools.values():
            s = tool.spec
            if not s.env_satisfied():
                continue
            if capability_policy and s.required_capabilities:
                if not all(
                    capability_policy.check(agent_id, cap, s.name)
                    for cap in s.required_capabilities
                ):
                    continue
            results.append(s)
        return results

    # -- Execution --------------------------------------------------------

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        agent_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool by name with middleware pipeline.

        Middleware functions are called in order before execution.
        Each can reject (return ToolResult with success=False) or pass through.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool_name=name,
                content=f"Unknown tool: {name}",
                success=False,
            )

        # Run pre-execution middleware (budget gate, RBAC, etc.)
        for mw in self._middleware:
            rejection = await mw(tool, params, agent_id, context or {})
            if rejection is not None:
                return rejection

        # Execute the tool
        import time

        t0 = time.time()
        try:
            result = tool.execute(**params)
        except Exception as exc:
            result = ToolResult(
                tool_name=name,
                content=f"Tool execution error: {exc}",
                success=False,
            )
        latency = time.time() - t0
        result.latency_seconds = latency

        # Run post-execution middleware (logging, cost tracking)
        for mw in self._post_middleware:
            try:
                await mw(tool, result, agent_id)
            except Exception as exc:
                logger.warning("Post-middleware error: %s", exc)

        return result

    # -- Middleware --------------------------------------------------------

    def add_middleware(self, fn: Callable) -> None:
        """Add a pre-execution middleware function.

        Signature: async (tool, params, agent_id, context) -> ToolResult | None
        Return None to pass through, or ToolResult to reject.
        """
        self._middleware.append(fn)

    def add_post_middleware(self, fn: Callable) -> None:
        """Add a post-execution middleware function.

        Signature: async (tool, result, agent_id) -> None
        """
        self._post_middleware.append(fn)

    # -- Introspection ----------------------------------------------------

    def get_tool_instance(self, name: str) -> UnifiedBaseTool | None:
        """Get the raw tool instance (for adapter introspection)."""
        return self._tools.get(name)

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return all available tools in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing only)."""
        if cls._instance is not None:
            cls._instance._tools.clear()
            cls._instance._middleware.clear()
            cls._instance._post_middleware.clear()
        cls._instance = None


# ═══════════════════════════════════════════════════════════════════════════
# Module-level singleton accessor
# ═══════════════════════════════════════════════════════════════════════════

registry = UnifiedToolRegistry()


def get_registry() -> UnifiedToolRegistry:
    """Get the singleton UnifiedToolRegistry."""
    return UnifiedToolRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# Budget Gating Middleware
# ═══════════════════════════════════════════════════════════════════════════


async def budget_gate_middleware(
    tool: UnifiedBaseTool,
    params: dict[str, Any],
    agent_id: str,
    context: dict[str, Any],
) -> ToolResult | None:
    """Reject tool execution if monthly budget is exceeded.

    Returns None to allow execution, or ToolResult to reject.
    """
    spec = tool.spec
    cost = spec.estimated_cost_per_call

    # Free tools always pass
    if cost <= 0.0:
        return None

    try:
        from tools.budget_guard import BudgetGuard

        guard = BudgetGuard()
        result = await guard.can_spend(cost, f"tool:{spec.name}")

        if not result.get("allowed", True):
            logger.warning(
                "Budget gate rejected tool '%s' (cost=$%.4f, reason=%s)",
                spec.name,
                cost,
                result.get("reason", "exceeded"),
            )
            return ToolResult(
                tool_name=spec.name,
                content=(
                    f"Budget exceeded: tool '{spec.name}' costs ~${cost:.4f} "
                    f"but only ${result.get('remaining', 0):.2f} remains. "
                    f"Reduce usage or increase budget cap."
                ),
                success=False,
                metadata={"rejection_reason": "budget_exceeded"},
            )
    except Exception as exc:
        # Fail open — don't block tools if budget check fails
        logger.warning("Budget check failed (allowing): %s", exc)

    return None


async def budget_log_middleware(
    tool: UnifiedBaseTool,
    result: ToolResult,
    agent_id: str,
) -> None:
    """Post-execution: log tool cost to budget_tracking and activity_log."""
    spec = tool.spec
    cost = getattr(result, "cost_usd", 0.0) or spec.estimated_cost_per_call

    if cost <= 0.0:
        return

    try:
        from shared.db import execute_query
        from datetime import date

        month = date.today().replace(day=1)
        await execute_query(
            """INSERT INTO budget_tracking (month, category, amount, description, pipeline_stage)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                month,
                f"tool:{spec.name}",
                cost,
                f"Tool execution by {agent_id}",
                spec.category,
            ),
        )
        await execute_query(
            """INSERT INTO activity_log (entity_type, action, details)
               VALUES ('tool', 'execute', %s::jsonb)""",
            (
                json.dumps(
                    {
                        "tool": spec.name,
                        "agent_id": agent_id,
                        "cost_usd": cost,
                        "tool_type": spec.tool_type.value,
                        "success": result.success,
                        "latency_seconds": getattr(result, "latency_seconds", 0),
                    }
                ),
            ),
        )
    except Exception as exc:
        logger.warning("Budget logging failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# Auto-Scan — Startup tool discovery and registration
# ═══════════════════════════════════════════════════════════════════════════

# Directories to scan for SKILL.md files
SKILL_SCAN_DIRS = [
    Path.home() / ".openclaw" / "skills",
    Path.home() / ".hermes" / "skills",
]

# Modules containing TASK_HANDLERS dicts
HANDLER_MODULES = [
    ("titan.daemon", "TASK_HANDLERS"),
    ("perseus.daemon", "TASK_HANDLERS"),
    ("clawdbot.daemon", "TASK_HANDLERS"),
    ("hermes.daemon", "TASK_HANDLERS"),
]

# Modules containing OJ tools or @register_tool decorated functions
TOOL_MODULES = [
    "openjarvis.tools",
    "tools.budget_guard",
]


def auto_scan_all(
    *,
    root_dir: Path | None = None,
    skip_oj: bool = False,
    skip_skills: bool = False,
    skip_handlers: bool = False,
) -> None:
    """Run all scanners and populate the UnifiedToolRegistry.

    Parameters
    ----------
    root_dir : Path | None
        Project root directory. Used to resolve relative skill paths.
    skip_oj : bool
        Skip OJ tool scanning (useful in tests).
    skip_skills : bool
        Skip SKILL.md scanning.
    skip_handlers : bool
        Skip TASK_HANDLERS scanning.
    """
    reg = get_registry()

    # Phase 1: Sync existing OJ ToolRegistry entries
    if not skip_oj:
        _scan_oj_tools(reg)

    # Phase 2: Flush @register_tool pending registrations
    _scan_decorated_tools(reg)

    # Phase 3: Scan SKILL.md files
    if not skip_skills:
        skill_dirs = list(SKILL_SCAN_DIRS)
        if root_dir:
            skill_dirs.extend(
                [
                    root_dir / "hermes" / "skills",
                    root_dir / ".agent" / "skills",
                ]
            )
        _scan_skill_files(reg, skill_dirs)

    # Phase 4: Wrap TASK_HANDLERS
    if not skip_handlers:
        _scan_handler_dicts(reg)

    logger.info("Auto-scan complete: %d tools registered", len(reg))


def _scan_oj_tools(reg: UnifiedToolRegistry) -> None:
    """Import OJ tools and sync to unified registry."""
    for mod_name in TOOL_MODULES:
        try:
            import importlib

            importlib.import_module(mod_name)
        except ImportError:
            logger.debug("Module %s not found, skipping", mod_name)

    try:
        from openjarvis.core.registry import ToolRegistry as OJToolRegistry

        for name, tool_cls in OJToolRegistry.items():
            try:
                if isinstance(tool_cls, type):
                    instance = tool_cls()
                else:
                    instance = tool_cls
                # Wrap OJ BaseTool as a HandlerToolAdapter if needed
                if hasattr(instance, "spec") and hasattr(instance, "execute"):
                    # Create a thin wrapper that bridges OJ BaseTool -> UnifiedBaseTool
                    oj_spec = instance.spec
                    adapter = HandlerToolAdapter(
                        handler_fn=instance.execute,
                        name=oj_spec.name,
                        schema=oj_spec.parameters,
                        description=oj_spec.description,
                        category=oj_spec.category,
                        cost_estimate=oj_spec.cost_estimate,
                        required_capabilities=oj_spec.required_capabilities,
                        source=f"oj:{name}",
                        tags=["native", oj_spec.category] if oj_spec.category else ["native"],
                        timeout_seconds=oj_spec.timeout_seconds,
                    )
                    reg.register(adapter)
            except Exception as exc:
                logger.warning("Failed to register OJ tool '%s': %s", name, exc)
    except ImportError:
        logger.debug("OJ registry not available, skipping OJ tool sync")


def _scan_decorated_tools(reg: UnifiedToolRegistry) -> None:
    """Flush pending @register_tool registrations."""
    for adapter in _PENDING_REGISTRATIONS:
        reg.register(adapter)
    _PENDING_REGISTRATIONS.clear()


def _scan_skill_files(
    reg: UnifiedToolRegistry, skill_dirs: list[Path]
) -> None:
    """Find all SKILL.md files and register as SkillToolAdapter."""
    for skill_dir in skill_dirs:
        if not skill_dir.exists():
            continue
        for skill_md in skill_dir.rglob("SKILL.md"):
            skill_name = skill_md.parent.name
            try:
                adapter = SkillToolAdapter(skill_md, skill_name)
                reg.register(adapter)
            except Exception as exc:
                logger.warning("Failed to register skill '%s': %s", skill_name, exc)


def _scan_handler_dicts(reg: UnifiedToolRegistry) -> None:
    """Import modules with TASK_HANDLERS and wrap each."""
    import importlib

    for mod_name, dict_name in HANDLER_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            logger.debug("Module %s not found, skipping", mod_name)
            continue

        handlers = getattr(mod, dict_name, {})
        daemon_name = mod_name.split(".")[0]

        for handler_name, handler_fn in handlers.items():
            tool_name = f"{daemon_name}:{handler_name}"
            adapter = HandlerToolAdapter(
                handler_fn=handler_fn,
                name=tool_name,
                category=daemon_name,
                source=f"handler:{mod_name}.{handler_name}",
                tags=[daemon_name, "handler", handler_name],
                timeout_seconds=300.0,
            )
            reg.register(adapter)


async def scan_mcp_servers(mcp_configs: list[dict[str, Any]]) -> None:
    """Discover and register tools from remote MCP servers.

    Called after async startup since MCP discovery requires network I/O.
    """
    reg = get_registry()
    for config in mcp_configs:
        try:
            # Import dynamically — MCP client may not be available
            from openjarvis.mcp.client import MCPClient  # type: ignore[import]

            client = MCPClient(
                config["url"], transport=config.get("transport", "stdio")
            )
            tools_response = await client.list_tools()
            for tool_def in tools_response.get("tools", []):
                adapter = MCPRemoteToolAdapter(client, tool_def, config["name"])
                reg.register(adapter)
            logger.info(
                "Registered %d tools from MCP server '%s'",
                len(tools_response.get("tools", [])),
                config["name"],
            )
        except Exception as exc:
            logger.warning(
                "Failed to discover MCP server '%s': %s",
                config.get("name", "?"),
                exc,
            )


# ═══════════════════════════════════════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "ToolType",
    "ToolSpec",
    "UnifiedBaseTool",
    "UnifiedToolRegistry",
    "SkillToolAdapter",
    "HandlerToolAdapter",
    "MCPRemoteToolAdapter",
    "register_tool",
    "get_registry",
    "registry",
    "auto_scan_all",
    "scan_mcp_servers",
    "budget_gate_middleware",
    "budget_log_middleware",
]
