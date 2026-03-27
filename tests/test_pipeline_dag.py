"""Tests for shared/pipeline_dag.py — constraint-aware pipeline transitions."""

import os

os.environ["PIPELINE_DAG_ENABLED"] = "1"

from shared.pipeline_dag import (
    STAGES,
    can_transition,
    get_pipeline_path,
    get_stage_constraints,
    validate_pipeline_state,
)

# ── can_transition ──

def test_valid_transition_discovered_to_researched():
    result = can_transition("discovered", "researched", {"business_name": "Acme"})
    assert result["allowed"] is True
    assert not result["missing_fields"]

def test_invalid_transition_discovered_to_closed():
    result = can_transition("discovered", "closed", {"business_name": "Acme"})
    assert result["allowed"] is False
    assert "Cannot transition" in result["reasons"][0]

def test_transition_missing_required_field():
    result = can_transition("discovered", "researched", {})
    assert result["allowed"] is False
    assert "business_name" in result["missing_fields"]

def test_transition_to_terminal_always_allowed():
    result = can_transition("email_sent", "lost", {})
    assert result["allowed"] is True

def test_transition_budget_check_flagged():
    result = can_transition("email_drafted", "email_sent", {"email": "a@b.com", "email_draft": "Hello"})
    assert "budget" in result["required_checks"]

def test_transition_human_approval_flagged():
    result = can_transition("negotiating", "closed", {"demo_site_url": "http://example.com"})
    assert "human_approval" in result["required_checks"]

def test_transition_unknown_stage():
    result = can_transition("discovered", "nonexistent_stage", {})
    assert result["allowed"] is False
    assert "Unknown" in result["reasons"][0]


# ── get_stage_constraints ──

def test_stage_constraints_discovered():
    constraints = get_stage_constraints("discovered")
    assert constraints is not None
    assert constraints["name"] == "discovered"
    assert constraints["handler_module"] == "titan.pipeline.lead_discovery"

def test_stage_constraints_nonexistent():
    assert get_stage_constraints("fake_stage") is None

def test_stage_constraints_closed_requires_approval():
    constraints = get_stage_constraints("closed")
    assert constraints["human_approval"] is True
    assert constraints["compliance_check"] is True


# ── get_pipeline_path ──

def test_path_discovered_to_paid():
    path = get_pipeline_path("discovered", "paid")
    assert path is not None
    assert path[0] == "discovered"
    assert path[-1] == "paid"
    assert len(path) >= 5  # at least 5 stages

def test_path_same_stage():
    path = get_pipeline_path("researched", "researched")
    assert path == ["researched"]

def test_path_no_valid_route():
    path = get_pipeline_path("paid", "discovered")
    assert path is None  # can't go backwards


# ── validate_pipeline_state ──

def test_validate_clean_client():
    client = {"status": "researched", "business_name": "Test Co"}
    violations = validate_pipeline_state(client)
    assert violations == []

def test_validate_missing_required_field():
    client = {"status": "researched"}  # missing business_name
    violations = validate_pipeline_state(client)
    assert len(violations) > 0
    assert "business_name" in violations[0]

def test_validate_no_status():
    violations = validate_pipeline_state({})
    assert "no status" in violations[0].lower()

def test_validate_unknown_status():
    violations = validate_pipeline_state({"status": "fake_status"})
    assert "Unknown" in violations[0]


# ── STAGES completeness ──

def test_all_stages_have_handler():
    for name, stage in STAGES.items():
        assert stage.handler_module, f"Stage {name} missing handler_module"

def test_all_stages_have_description():
    for name, stage in STAGES.items():
        assert stage.description, f"Stage {name} missing description"
