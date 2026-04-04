"""Tests for tools/claude_code_tool.py — Claude Agent SDK wrapper."""

import os
import sys
import types
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import fields

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.claude_code_tool import (
    agent_sdk_available,
    AgentResult,
    run_agent_task,
    build_site,
    generate_code,
)


# ---------------------------------------------------------------------------
# 1. agent_sdk_available — SDK present + key set
# ---------------------------------------------------------------------------
def test_agent_sdk_available_with_key_and_sdk():
    """Returns True when both ANTHROPIC_API_KEY and the SDK package exist."""
    fake_sdk = types.ModuleType("claude_agent_sdk")
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}):
        with patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
            assert agent_sdk_available() is True


# ---------------------------------------------------------------------------
# 2. agent_sdk_available — no API key
# ---------------------------------------------------------------------------
def test_agent_sdk_available_no_key():
    """Returns False when ANTHROPIC_API_KEY is missing."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        assert agent_sdk_available() is False


# ---------------------------------------------------------------------------
# 3. agent_sdk_available — SDK not installed
# ---------------------------------------------------------------------------
def test_agent_sdk_available_no_sdk():
    """Returns False when claude_agent_sdk cannot be imported."""
    # Ensure the real module (if any) is hidden and the import fails
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}):
        with patch.dict(sys.modules, {"claude_agent_sdk": None}):
            # When sys.modules maps a name to None, import raises ImportError
            assert agent_sdk_available() is False


# ---------------------------------------------------------------------------
# 4. run_agent_task — SDK not installed → graceful error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_agent_task_no_sdk():
    """Returns error AgentResult when SDK is not installed."""
    with patch.dict(sys.modules, {"claude_agent_sdk": None}):
        result = await run_agent_task(prompt="test")
        assert result.success is False
        assert "not installed" in result.error


# ---------------------------------------------------------------------------
# 5. run_agent_task — no API key → graceful error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_agent_task_no_key():
    """Returns error AgentResult when ANTHROPIC_API_KEY is missing."""
    # Provide a real-enough fake SDK so the import succeeds
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.query = MagicMock()
    fake_sdk.ClaudeAgentOptions = MagicMock()
    with patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}):
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            result = await run_agent_task(prompt="test")
            assert result.success is False
            assert "ANTHROPIC_API_KEY" in result.error


# ---------------------------------------------------------------------------
# 6. AgentResult dataclass defaults
# ---------------------------------------------------------------------------
def test_agent_result_dataclass():
    """AgentResult initializes with correct defaults and is mutable."""
    r = AgentResult(success=True)
    assert r.success is True
    assert r.output == ""
    assert r.files_created == []
    assert r.files_modified == []
    assert r.cost_usd == 0.0
    assert r.turns_used == 0
    assert r.error == ""

    # Mutability
    r.files_created.append("/tmp/index.html")
    assert len(r.files_created) == 1

    # Ensure independent default lists across instances
    r2 = AgentResult(success=False)
    assert r2.files_created == []


# ---------------------------------------------------------------------------
# 7. build_site — prompt composition
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_site_creates_prompt():
    """build_site composes a prompt that includes the business brief details."""
    captured = {}

    async def fake_run_agent_task(**kwargs):
        captured.update(kwargs)
        return AgentResult(success=True, output="site built")

    brief = {
        "business_name": "Acme Plumbing",
        "industry": "plumbing",
        "services": ["drain cleaning", "pipe repair"],
        "pages": ["home", "services"],
        "tone": "friendly",
    }

    with patch("tools.claude_code_tool.run_agent_task", side_effect=fake_run_agent_task):
        result = await build_site(brief=brief, output_dir="/tmp/test-site")

    assert result.success is True
    # Verify prompt includes business details
    prompt = captured["prompt"]
    assert "Acme Plumbing" in prompt
    assert "plumbing" in prompt
    assert "drain cleaning" in prompt
    assert "pipe repair" in prompt
    assert "home" in prompt
    assert "services" in prompt
    assert "friendly" in prompt
    # Verify budget and model
    assert captured["max_budget_usd"] == 3.0
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["cwd"] == "/tmp/test-site"


# ---------------------------------------------------------------------------
# 8. build_site — 21st.dev MCP integration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_site_includes_21st_dev_mcp():
    """When TWENTYFIRST_API_KEY is set, MCP servers and tools are configured."""
    captured = {}

    async def fake_run_agent_task(**kwargs):
        captured.update(kwargs)
        return AgentResult(success=True, output="site built")

    brief = {"business_name": "Test Biz", "industry": "tech"}

    with patch("tools.claude_code_tool.run_agent_task", side_effect=fake_run_agent_task):
        with patch.dict(os.environ, {"TWENTYFIRST_API_KEY": "test-21st-key"}):
            await build_site(brief=brief, use_21st_dev=True)

    # MCP server should be configured
    assert "@21st-dev/magic" in captured["mcp_servers"]
    server_cfg = captured["mcp_servers"]["@21st-dev/magic"]
    assert server_cfg["command"] == "npx"
    assert server_cfg["env"]["API_KEY"] == "test-21st-key"

    # Allowed tools should include 21st.dev tools
    assert "mcp__@21st-dev/magic__21st_magic_component_builder" in captured["allowed_tools"]
    assert "mcp__@21st-dev/magic__21st_magic_component_inspiration" in captured["allowed_tools"]


# ---------------------------------------------------------------------------
# 9. generate_code — basic invocation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_code_basic():
    """generate_code delegates to run_agent_task with correct defaults."""
    captured = {}

    async def fake_run_agent_task(**kwargs):
        captured.update(kwargs)
        return AgentResult(success=True, output="code generated")

    with patch("tools.claude_code_tool.run_agent_task", side_effect=fake_run_agent_task):
        result = await generate_code(task="add logging", cwd="/tmp/proj")

    assert result.success is True
    assert captured["prompt"] == "add logging"
    assert captured["cwd"] == "/tmp/proj"
    assert captured["max_budget_usd"] == 1.0
    assert captured["max_turns"] == 30
