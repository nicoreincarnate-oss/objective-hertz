"""Tests for Task 19-05 (speculative parallel execution) and Task 18b-03 (operative prompt caching).

Task 19-05: speculative research queue
- test_speculative_early_lead_queued: high-score lead pushed to queue
- test_speculative_low_score_skipped: low-score lead not queued
- test_speculative_results_cached: cached results reused

Task 18b-03: operative prompt caching
- test_operative_prompt_cached: second tick reuses cached prefix
- test_operative_volatile_changes: volatile suffix changes per tick
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Patch openjarvis.core.types for Python < 3.10 (slots=True unsupported)
# ---------------------------------------------------------------------------

def _ensure_operative_importable():
    """Ensure OperativeAgent can be imported even on Python < 3.10.

    The openjarvis package __init__ transitively imports core/types.py
    which uses @dataclass -- a Python 3.10+ feature.  We
    pre-populate sys.modules with lightweight stubs so the operative
    module can be imported without hitting that error.
    """
    if "openjarvis.agents.operative" in sys.modules:
        return  # already imported

    # Check if the full import works natively
    try:
        import openjarvis.agents.operative  # noqa: F401
        return
    except (TypeError, ModuleNotFoundError, ImportError, AttributeError):
        pass

    # Install stubs for the problematic chain
    from types import ModuleType
    from enum import Enum
    from dataclasses import dataclass

    # Stub core.types
    class Role(str, Enum):
        SYSTEM = "system"
        USER = "user"
        ASSISTANT = "assistant"
        TOOL = "tool"

    @dataclass
    class Message:
        role: Role = Role.USER
        content: str = ""
        tool_calls: list = None
        tool_call_id: str = ""
        name: str = ""
        def __post_init__(self):
            if self.tool_calls is None:
                self.tool_calls = []

    @dataclass
    class ToolCall:
        id: str = ""
        name: str = ""
        arguments: str = "{}"

    @dataclass
    class ToolResult:
        tool_name: str = ""
        content: str = ""
        success: bool = True

    # Build stub modules (force-set to override any flat stubs from other tests)
    for mod_path in [
        "openjarvis", "openjarvis.core", "openjarvis.core.types",
        "openjarvis.core.config", "openjarvis.core.registry",
        "openjarvis.core.events", "openjarvis.agents",
        "openjarvis.agents._stubs", "openjarvis.engine",
        "openjarvis.engine._stubs", "openjarvis.tools",
        "openjarvis.tools._stubs", "openjarvis.sdk",
    ]:
        if mod_path not in sys.modules or not hasattr(sys.modules.get(mod_path, None), "__path__"):
            sys.modules[mod_path] = ModuleType(mod_path)

    types_mod = sys.modules["openjarvis.core.types"]
    types_mod.Role = Role
    types_mod.Message = Message
    types_mod.ToolCall = ToolCall
    types_mod.ToolResult = ToolResult

    # Stub agents._stubs
    class AgentContext:
        def __init__(self):
            self.conversation = MagicMock()
            self.conversation.messages = []

    @dataclass
    class AgentResult:
        content: str = ""
        tool_results: list = None
        turns: int = 0
        metadata: dict = None
        def __post_init__(self):
            if self.tool_results is None:
                self.tool_results = []
            if self.metadata is None:
                self.metadata = {}

    class ToolUsingAgent:
        accepts_tools = True
        agent_id = "base"
        def __init__(self, engine, model, *, tools=None, bus=None,
                     max_turns=20, temperature=0.3, max_tokens=2048,
                     interactive=False, confirm_callback=None):
            self._engine = engine
            self._model = model
            self._tools = tools or []
            self._max_turns = max_turns
            self._temperature = temperature
            self._max_tokens = max_tokens
            self._loop_guard = None
            self._executor = MagicMock()
        def _emit_turn_start(self, *a): pass
        def _emit_turn_end(self, **kw): pass
        def _generate(self, messages, **kwargs):
            return self._engine.generate(messages, **kwargs) if hasattr(self._engine, 'generate') else {"content": "", "tool_calls": []}
        def _check_continuation(self, result, messages):
            return result.get("content", "")

    stubs_mod = sys.modules["openjarvis.agents._stubs"]
    stubs_mod.AgentContext = AgentContext
    stubs_mod.AgentResult = AgentResult
    stubs_mod.ToolUsingAgent = ToolUsingAgent

    # Stub core.events / core.registry / engine._stubs / tools._stubs
    class EventBus:
        pass
    sys.modules["openjarvis.core.events"].EventBus = EventBus

    class AgentRegistry:
        @staticmethod
        def register(name):
            def decorator(cls):
                return cls
            return decorator
    sys.modules["openjarvis.core.registry"].AgentRegistry = AgentRegistry

    class InferenceEngine:
        pass
    sys.modules["openjarvis.engine._stubs"].InferenceEngine = InferenceEngine

    class BaseTool:
        pass
    sys.modules["openjarvis.tools._stubs"].BaseTool = BaseTool

    # Now import the operative module directly
    spec = importlib.util.spec_from_file_location(
        "openjarvis.agents.operative",
        os.path.join(os.path.dirname(__file__), "..", "openjarvis", "agents", "operative.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["openjarvis.agents.operative"] = mod
    spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Task 19-05: Speculative research queue tests
# ---------------------------------------------------------------------------


class TestSpeculativeEarlyLeadQueued:
    """High-score lead pushed to the speculative queue."""

    def test_speculative_early_lead_queued(self):
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()
            # lead_score 75 out of 100 -> normalised 0.75 >= 0.6 threshold
            lead = {"id": 42, "business_name": "Acme Plumbing", "lead_score": 75}
            assert queue.should_enqueue(lead) is True

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(queue.enqueue(lead))
                assert result is True
                assert queue.queue.qsize() == 1
            finally:
                loop.close()

    def test_speculative_early_lead_queued_float_score(self):
        """Score already in 0-1 range."""
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()
            lead = {"id": 43, "business_name": "Bob's Diner", "lead_score": 0.8}
            assert queue.should_enqueue(lead) is True


class TestSpeculativeLowScoreSkipped:
    """Low-score lead not pushed to the speculative queue."""

    def test_speculative_low_score_skipped(self):
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()
            # lead_score 30 out of 100 -> normalised 0.30 < 0.6 threshold
            lead = {"id": 99, "business_name": "Low Score LLC", "lead_score": 30}
            assert queue.should_enqueue(lead) is False

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(queue.enqueue(lead))
                assert result is False
                assert queue.queue.qsize() == 0
            finally:
                loop.close()

    def test_speculative_skipped_when_flag_off(self):
        """Even high-score leads are skipped when the flag is off."""
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": ""}, clear=False):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()
            lead = {"id": 44, "business_name": "High Score Inc", "lead_score": 90}
            assert queue.should_enqueue(lead) is False


class TestSpeculativeResultsCached:
    """Consumer populates cache; formal research reuses it."""

    def test_speculative_results_cached(self):
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()

            # Simulate the consumer having already researched lead 42
            cached_result = {
                "research_data": {
                    "summary": "Acme Plumbing is a family-owned plumber.",
                    "lead_score": 80,
                    "estimated_industry": "plumbing",
                    "language": "en",
                },
            }
            queue.cache[42] = cached_result

            # Research stage should find it
            result = queue.get_cached(42)
            assert result is not None
            assert result["research_data"]["summary"] == "Acme Plumbing is a family-owned plumber."
            assert result["research_data"]["lead_score"] == 80

            # Non-existent lead returns None
            assert queue.get_cached(999) is None

    def test_consumer_populates_cache(self):
        """Full consumer loop: enqueue -> consume -> cache populated."""
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}):
            from titan.pipeline.speculative_research import (
                SpeculativeResearchQueue,
            )

            queue = SpeculativeResearchQueue()

            async def mock_research_fn(lead):
                return {
                    "summary": f"Researched {lead['business_name']}",
                    "lead_score": 85,
                }

            async def _run():
                lead = {"id": 55, "business_name": "Test Biz", "lead_score": 80}
                await queue.enqueue(lead)
                await queue.start_consumer(mock_research_fn)
                # Give the consumer a moment to process
                await asyncio.sleep(0.2)
                await queue.stop_consumer()
                return queue.get_cached(55)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_run())
                assert result is not None
                assert result["summary"] == "Researched Test Biz"
                assert result["lead_score"] == 85
            finally:
                loop.close()


# ---------------------------------------------------------------------------
# Task 18b-03: Operative prompt caching tests
# ---------------------------------------------------------------------------


def _make_operative(system_prompt="You are TestBot.", operator_id="test-op"):
    """Create a minimal OperativeAgent with mocked dependencies."""
    _ensure_operative_importable()
    from openjarvis.agents.operative import OperativeAgent

    engine = MagicMock()
    agent = OperativeAgent(
        engine=engine,
        model="test-model",
        system_prompt=system_prompt,
        operator_id=operator_id,
        tools=None,
        memory_backend=None,
        session_store=None,
    )
    return agent


class TestOperativePromptCached:
    """Second tick reuses cached stable prefix."""

    def test_operative_prompt_cached(self):
        with patch.dict(os.environ, {"ANATOMY_PROMPT_CACHE": "true"}):
            agent = _make_operative(system_prompt="You are Perseus daemon.")

            # First tick: prefix should be cached
            prompt1 = agent._build_cached_system_prompt()
            assert prompt1 is not None
            assert "You are Perseus daemon." in prompt1
            assert agent._cached_stable_prefix == "You are Perseus daemon."

            # Second tick: should reuse the same cached prefix object
            prompt2 = agent._build_cached_system_prompt()
            assert prompt2 is not None
            assert agent._cached_stable_prefix is not None
            # The stable prefix should be the exact same object (identity check)
            assert agent._cached_stable_prefix == "You are Perseus daemon."

    def test_operative_prompt_not_cached_when_flag_off(self):
        with patch.dict(os.environ, {"ANATOMY_PROMPT_CACHE": ""}, clear=False):
            agent = _make_operative(system_prompt="You are Perseus daemon.")

            prompt = agent._build_cached_system_prompt()
            assert prompt is not None
            assert "You are Perseus daemon." in prompt
            # No caching should have occurred
            assert agent._cached_stable_prefix is None


class TestOperativeVolatileChanges:
    """Volatile suffix (previous state) changes per tick while prefix stays stable."""

    def test_operative_volatile_changes(self):
        with patch.dict(os.environ, {"ANATOMY_PROMPT_CACHE": "true"}):
            mock_backend = MagicMock()
            agent = _make_operative(system_prompt="You are Titan.")
            agent._memory_backend = mock_backend

            # Tick 1: no previous state
            mock_backend.retrieve.return_value = None
            prompt1 = agent._build_cached_system_prompt()
            assert prompt1 == "You are Titan."
            assert agent._cached_stable_prefix == "You are Titan."

            # Tick 2: state changes -> volatile suffix changes
            mock_backend.retrieve.return_value = "pipeline_stage=discover, leads=5"
            prompt2 = agent._build_cached_system_prompt()
            assert "You are Titan." in prompt2
            assert "pipeline_stage=discover" in prompt2
            # The stable prefix is still the same
            assert agent._cached_stable_prefix == "You are Titan."

            # Tick 3: state changes again
            mock_backend.retrieve.return_value = "pipeline_stage=research, leads=10"
            prompt3 = agent._build_cached_system_prompt()
            assert "You are Titan." in prompt3
            assert "pipeline_stage=research" in prompt3
            # Previous state from tick 2 should NOT appear in tick 3
            assert "pipeline_stage=discover" not in prompt3

            # All three prompts should share the same prefix
            assert prompt1 != prompt2  # volatile part differs
            assert prompt2 != prompt3  # volatile part changed again
