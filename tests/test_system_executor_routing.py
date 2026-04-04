"""Regression tests for system executor routing and A2A config wiring."""

from __future__ import annotations

import importlib
import sys


def test_task_routing_includes_system_executor_capabilities():
    from shared.task_routing import TASK_ROUTING

    assert TASK_ROUTING["desktop_control"] == "system_executor"
    assert TASK_ROUTING["desktop_exec"] == "system_executor"
    assert TASK_ROUTING["system_action"] == "system_executor"
    assert TASK_ROUTING["auth_checkpoint"] == "system_executor"
    assert TASK_ROUTING["screen_context"] == "system_executor"
    assert TASK_ROUTING["health_check"] == "system_executor"
    assert TASK_ROUTING["evolution_research_cycle"] == "deerflow_research"
    assert TASK_ROUTING["paper_scan"] == "deerflow_research"
    assert TASK_ROUTING["repo_scan"] == "deerflow_research"
    assert TASK_ROUTING["daily_evolution_brief"] == "deerflow_research"


def test_agent_urls_include_system_executor_override(monkeypatch):
    monkeypatch.setenv("SYSTEM_EXECUTOR_A2A_URL", "http://sidecar.local:9010")
    monkeypatch.setenv("DEERFLOW_RESEARCH_A2A_URL", "http://deerflow.local:9011")

    sys.modules.pop("shared.oj_bridge", None)
    bridge = importlib.import_module("shared.oj_bridge")

    assert bridge.AGENT_URLS["system_executor"] == "http://sidecar.local:9010"
    assert bridge.AGENT_URLS["deerflow_research"] == "http://deerflow.local:9011"
