from hermes.web.kirito_runtime import build_status_graph, normalize_dispatch_plan, serialize_route_plan


class _LegacyPlan:
    intent = "browser_task"
    task_type = "browser_task"
    capability = "browser_flow"
    target_agent = "clawdbot"
    local_action = False
    requires_followup = False
    followup_question = None
    payload = {"browser_goal": "download images"}
    signals = ("browser",)
    confidence = 0.91
    rationale = "Browser work detected."


def test_serialize_route_plan_backfills_new_metadata():
    payload = serialize_route_plan(_LegacyPlan())

    assert payload["intent"] == "browser_task"
    assert payload["domain"] == "browser"
    assert payload["executor"] == "clawdbot"
    assert payload["risk_class"] == "medium"
    assert payload["capability_family"] == "browser.*"
    assert payload["supports_full_autonomy"] is True


def test_status_graph_marks_auth_wait_and_keeps_artifacts():
    result = build_status_graph(
        events=[
            {
                "event_type": "kirito_command_received",
                "created_at": "2026-04-01T10:00:00",
                "payload": {"request_id": "abc"},
            },
            {
                "event_type": "kirito_command_planned",
                "created_at": "2026-04-01T10:00:01",
                "payload": {
                    "request_id": "abc",
                    "selected_executor": "browser_use",
                    "target_agent": "clawdbot",
                    "task_type": "browser_flow",
                },
            },
            {
                "event_type": "executor_auth_wait",
                "created_at": "2026-04-01T10:00:02",
                "payload": {
                    "request_id": "abc",
                    "channel": "google",
                    "reason": "MFA required",
                    "artifact": {"type": "link", "url": "https://example.test"},
                },
            },
        ],
        tasks=[],
    )

    assert result["state"] == "auth_wait"
    assert result["auth_waits"][0]["channel"] == "google"
    assert result["artifacts"][0]["url"] == "https://example.test"
    assert len(result["steps"]) == 3


def test_status_graph_promotes_task_queue_activity_to_running():
    result = build_status_graph(
        events=[
            {
                "event_type": "kirito_command_dispatched",
                "created_at": "2026-04-01T10:00:00",
                "payload": {"request_id": "abc", "target_agent": "ruflo"},
            }
        ],
        tasks=[
            {
                "id": 1,
                "task_type": "repo_execute",
                "status": "running",
                "payload": {"request_id": "abc"},
                "created_at": "2026-04-01T10:00:01",
                "updated_at": "2026-04-01T10:00:02",
            }
        ],
    )

    assert result["state"] == "running"
    assert any(span["event_type"] == "task_queue" for span in result["spans"])


def test_normalize_dispatch_plan_maps_broad_intents_to_real_executors():
    base = serialize_route_plan(
        {
            "intent": "workflow_task",
            "target_agent": "orchestrator",
            "task_type": "workflow_task",
            "capability": "workflow_task",
            "domain": "workflow",
            "executor": "orchestrator",
            "risk": "medium",
            "supports_auth": False,
            "supports_full_autonomy": False,
            "visibility_mode": "audited",
            "fallback_executor": "hermes",
            "capability_family": "workflow",
            "local_action": False,
            "payload": {"source_text": "Run the onboarding workflow in n8n."},
        }
    )

    normalized = normalize_dispatch_plan(base)

    assert normalized["target_agent"] == "clawdbot"
    assert normalized["task_type"] == "workflow_run"
    assert normalized["capability"] == "workflow_run"
    assert normalized["capability_family"] == "workflow.*"


def test_normalize_dispatch_plan_maps_research_to_deerflow():
    base = serialize_route_plan(
        {
            "intent": "research_and_apply",
            "target_agent": "orchestrator",
            "task_type": "operator_command",
            "capability": "operator_command",
            "domain": "research",
            "executor": "orchestrator",
            "risk": "medium",
            "supports_auth": False,
            "supports_full_autonomy": False,
            "visibility_mode": "audited",
            "fallback_executor": "hermes",
            "capability_family": "research",
            "local_action": False,
            "payload": {"source_text": "Research new AI papers and tell me how Perseus should evolve."},
        }
    )

    normalized = normalize_dispatch_plan(base)

    assert normalized["target_agent"] == "deerflow_research"
    assert normalized["task_type"] == "evolution_research_cycle"
    assert normalized["capability"] == "evolution_research_cycle"
    assert normalized["capability_family"] == "research.*"
