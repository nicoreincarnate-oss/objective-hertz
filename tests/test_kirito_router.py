import pytest

from hermes.web.kirito_router import (
    KiritoRoutingHooks,
    route_kirito_command,
)


def test_routes_sales_dashboard_to_hermes_local_action():
    plan = route_kirito_command("Pull up the sales dashboard so I can see our numbers.")

    assert plan.intent == "open_dashboard"
    assert plan.target_agent == "hermes"
    assert plan.task_type == "open_dashboard"
    assert plan.capability == "operator_command"
    assert plan.domain == "desktop"
    assert plan.executor == "hermes"
    assert plan.risk == "low"
    assert plan.supports_auth is True
    assert plan.supports_full_autonomy is True
    assert plan.visibility_mode == "local"
    assert plan.fallback_executor == "orchestrator"
    assert plan.capability_family == "desktop"
    assert plan.local_action is True
    assert plan.requires_followup is False
    assert plan.payload["dashboard"] == "sales"
    assert plan.payload["source_text"].startswith("Pull up the sales dashboard")


def test_routes_titan_audit_to_orchestrator():
    plan = route_kirito_command("Audit Titan's workflow for the last three hours.")

    assert plan.intent == "audit_workflow"
    assert plan.target_agent == "orchestrator"
    assert plan.task_type == "operator_command"
    assert plan.capability == "operator_command"
    assert plan.domain == "workflow"
    assert plan.executor == "orchestrator"
    assert plan.risk == "medium"
    assert plan.supports_auth is False
    assert plan.supports_full_autonomy is False
    assert plan.visibility_mode == "audited"
    assert plan.fallback_executor == "hermes"
    assert plan.capability_family == "workflow"
    assert plan.local_action is False
    assert plan.requires_followup is False
    assert plan.payload["subject_agent"] == "titan"
    assert plan.payload["time_window_hours"] == 3


def test_routes_research_to_orchestrator_with_time_window():
    plan = route_kirito_command("Research new AI papers from the last three days and apply them.")

    assert plan.intent == "research_and_apply"
    assert plan.target_agent == "orchestrator"
    assert plan.task_type == "operator_command"
    assert plan.capability == "operator_command"
    assert plan.domain == "research"
    assert plan.executor == "orchestrator"
    assert plan.risk == "medium"
    assert plan.supports_auth is False
    assert plan.supports_full_autonomy is False
    assert plan.visibility_mode == "audited"
    assert plan.fallback_executor == "hermes"
    assert plan.capability_family == "research"
    assert plan.local_action is False
    assert plan.requires_followup is False
    assert plan.payload["topic"] == "new ai papers"
    assert plan.payload["time_window_days"] == 3
    assert plan.payload["desired_outcome"] == "apply"
    assert plan.payload["preferred_local_model"] == "local-heavy"
    assert plan.payload["preferred_inference_lane"] == "airllm"


def test_routes_browser_download_work_to_clawdbot():
    plan = route_kirito_command("Use ClaudeBot to open the browser and download 17 pictures from the internet.")

    assert plan.intent == "browser_task"
    assert plan.target_agent == "clawdbot"
    assert plan.task_type == "browser_task"
    assert plan.capability == "browser_task"
    assert plan.domain == "browser"
    assert plan.executor == "clawdbot"
    assert plan.risk == "medium"
    assert plan.supports_auth is True
    assert plan.supports_full_autonomy is True
    assert plan.visibility_mode == "visible"
    assert plan.fallback_executor == "orchestrator"
    assert plan.capability_family == "browser"
    assert plan.local_action is False
    assert plan.payload["requested_count"] == 17
    assert "browser" in plan.signals


def test_routes_code_work_to_ruflo():
    plan = route_kirito_command("Fix the bug in the command router and add tests.")

    assert plan.intent == "code_task"
    assert plan.target_agent == "ruflo"
    assert plan.task_type == "code_fix"
    assert plan.capability == "code_fix"
    assert plan.domain == "repo"
    assert plan.executor == "ruflo"
    assert plan.risk == "medium"
    assert plan.supports_auth is True
    assert plan.supports_full_autonomy is False
    assert plan.visibility_mode == "audited"
    assert plan.fallback_executor == "orchestrator"
    assert plan.capability_family == "repo"
    assert plan.local_action is False
    assert plan.payload["source_text"].startswith("Fix the bug")


@pytest.mark.parametrize(
    "text, intent, payload_key, domain, executor, risk, supports_auth, supports_full_autonomy, visibility_mode, fallback_executor, capability_family",
    [
        (
            "Open the desktop terminal window and focus it.",
            "desktop_task",
            "desktop_goal",
            "desktop",
            "hermes",
            "low",
            True,
            True,
            "local",
            "orchestrator",
            "desktop",
        ),
        (
            "Check the system service and restart it if needed.",
            "system_task",
            "system_goal",
            "system",
            "orchestrator",
            "high",
            True,
            False,
            "audited",
            "hermes",
            "system",
        ),
        (
            "Run the onboarding workflow in n8n and keep it active.",
            "workflow_task",
            "workflow_goal",
            "workflow",
            "orchestrator",
            "medium",
            False,
            False,
            "audited",
            "hermes",
            "workflow",
        ),
        (
            "Update the repo README and commit the change.",
            "repo_task",
            "repo_goal",
            "repo",
            "ruflo",
            "medium",
            True,
            False,
            "audited",
            "orchestrator",
            "repo",
        ),
        (
            "What do you remember about meat?",
            "memory_query",
            "query",
            "memory",
            "orchestrator",
            "low",
            False,
            True,
            "private",
            "hermes",
            "memory",
        ),
        (
            "Observe the logs and report anomalies.",
            "observe_task",
            "observation_goal",
            "observe",
            "orchestrator",
            "low",
            False,
            True,
            "public",
            "hermes",
            "observe",
        ),
    ],
)
def test_routes_new_intent_families(
    text: str,
    intent: str,
    payload_key: str,
    domain: str,
    executor: str,
    risk: str,
    supports_auth: bool,
    supports_full_autonomy: bool,
    visibility_mode: str,
    fallback_executor: str,
    capability_family: str,
):
    plan = route_kirito_command(text)

    assert plan.intent == intent
    assert plan.domain == domain
    assert plan.executor == executor
    assert plan.risk == risk
    assert plan.supports_auth is supports_auth
    assert plan.supports_full_autonomy is supports_full_autonomy
    assert plan.visibility_mode == visibility_mode
    assert plan.fallback_executor == fallback_executor
    assert plan.capability_family == capability_family
    assert plan.payload[payload_key] == text
    if intent in {"memory_query", "observe_task"}:
        assert plan.payload["preferred_local_model"] == "local-heavy"
        assert plan.payload["preferred_inference_lane"] == "airllm"


def test_hooks_can_override_intent_and_agent():
    hooks = KiritoRoutingHooks(
        intent_override=lambda text, context: "memory_query" if "remember" in text.lower() else None,
        agent_override=lambda plan, text, context: "hermes" if plan.intent == "memory_query" else None,
    )

    plan = route_kirito_command("What do you remember about meat?", hooks=hooks)

    assert plan.intent == "memory_query"
    assert plan.target_agent == "hermes"
    assert plan.domain == "memory"
    assert plan.executor == "orchestrator"
    assert plan.risk == "low"
    assert plan.supports_auth is False
    assert plan.supports_full_autonomy is True
    assert plan.visibility_mode == "private"
    assert plan.fallback_executor == "hermes"
    assert plan.capability_family == "memory"
    assert plan.requires_followup is False
    assert plan.payload["query"] == "What do you remember about meat?"
