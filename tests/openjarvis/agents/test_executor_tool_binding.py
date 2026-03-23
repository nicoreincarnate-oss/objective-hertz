"""Tests for AgentExecutor runtime tool binding and routing."""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents._stubs import AgentResult, ToolUsingAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.tools._stubs import BaseTool, ToolSpec


class _DummyTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell_exec",
            description="dummy shell tool",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params):
        raise NotImplementedError


class _CapturingAgent(ToolUsingAgent):
    agent_id = "capturing_agent"
    captured = {}

    def __init__(self, engine, model, **kwargs):
        type(self).captured = {
            "engine": engine,
            "model": model,
            "kwargs": kwargs,
        }
        super().__init__(engine, model, **kwargs)

    def run(self, input: str, context=None, **kwargs):
        return AgentResult(content="ok")


def test_executor_builds_tools_from_agent_config(tmp_path):
    from openjarvis.agents.executor import AgentExecutor
    from openjarvis.agents.manager import AgentManager

    AgentRegistry.register_value("capturing_agent", _CapturingAgent)

    mgr = AgentManager(str(tmp_path / "agents.db"))
    bus = EventBus()
    executor = AgentExecutor(mgr, bus)

    fake_engine = MagicMock()
    fake_engine.list_models.return_value = ["model-a", "model-b"]

    fake_system = MagicMock()
    fake_system.engine = fake_engine
    fake_system.model = "model-a"
    fake_system.capability_policy = None
    fake_system.session_store = None
    fake_system.memory_backend = None
    fake_system.config.agent.context_from_memory = False
    fake_system._build_tools.return_value = [_DummyTool()]
    executor.set_system(fake_system)

    agent = mgr.create_agent(
        name="tool-bound",
        agent_type="capturing_agent",
        config={
            "tools": ["shell_exec"],
            "max_turns": 7,
            "temperature": 0.1,
            "max_tokens": 222,
        },
    )

    result = executor._invoke_agent(agent)

    assert result.content == "ok"
    fake_system._build_tools.assert_called_once_with(["shell_exec"])
    captured = _CapturingAgent.captured["kwargs"]
    assert captured["max_turns"] == 7
    assert captured["temperature"] == 0.1
    assert captured["max_tokens"] == 222
    assert len(captured["tools"]) == 1
    assert captured["tools"][0].spec.name == "shell_exec"
    mgr.close()
