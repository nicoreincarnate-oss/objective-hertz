"""Tests for the self-audit system."""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# Fake modules
_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.execute = AsyncMock()

_fake_llm_mod = types.ModuleType("shared.llm_client")
_fake_llm_mod.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"findings": []}'))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.ask_agent = AsyncMock(return_value={"answer": "approve"})
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.store_learning = AsyncMock()
_fake_comms.delegate_task = AsyncMock(return_value=1)

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(root_dir="/tmp/test-repo")

sys.modules.setdefault("shared.db", _fake_db)
sys.modules.setdefault("shared.llm_client", _fake_llm_mod)
sys.modules.setdefault("shared.comms", _fake_comms)
sys.modules.setdefault("shared.config", _fake_config)

from perseus.self_audit import (
    AUDIT_IMMUTABLE,
    FAILURE_MODES,
    _deduplicate,
    _parse_vote,
)


def run(coro):
    return asyncio.run(coro)


def test_parse_vote_extracts_approve():
    assert _parse_vote({"answer": "I approve this fix"}) == "approve"
    assert _parse_vote({"answer": "Yes, we should fix this"}) == "approve"
    assert _parse_vote({"vote": "approve"}) == "approve"


def test_parse_vote_extracts_reject():
    assert _parse_vote({"answer": "I reject this change — it could break the pipeline"}) == "reject"
    assert _parse_vote({"answer": "No, we should not change this"}) == "reject"
    assert _parse_vote({"vote": "reject"}) == "reject"


def test_parse_vote_defaults_to_defer():
    assert _parse_vote(None) == "defer"
    assert _parse_vote({}) == "defer"
    assert _parse_vote({"answer": "I'm not sure about this"}) == "defer"


def test_deduplicate_removes_same_file_issue():
    findings = [
        {"file": "shared/comms.py", "issue": "SQL injection in interval"},
        {"file": "shared/comms.py", "issue": "SQL injection in interval"},
        {"file": "shared/comms.py", "issue": "Different issue"},
    ]
    unique = _deduplicate(findings)
    assert len(unique) == 2


def test_immutable_files_include_self():
    assert "perseus/self_audit.py" in AUDIT_IMMUTABLE
    assert "perseus/backprop.py" in AUDIT_IMMUTABLE
    assert "orchestrator.py" in AUDIT_IMMUTABLE
    assert "clawdbot/safety.py" in AUDIT_IMMUTABLE


def test_failure_modes_cover_known_patterns():
    assert "dead_code" in FAILURE_MODES
    assert "json_parsing_fragility" in FAILURE_MODES
    assert "silent_swallowing" in FAILURE_MODES
    assert "column_order_bugs" in FAILURE_MODES
    assert len(FAILURE_MODES) == 12


def test_consensus_requires_two_approvals():
    """Verify the consensus logic from _discuss_with_agents."""
    from perseus import self_audit

    finding = {
        "file": "test.py",
        "issue": "test issue",
        "severity": "medium",
        "failure_mode": "dead_code",
        "proposed_fix": "remove function",
        "fix_type": "code_edit",
        "reasoning": "never called",
    }

    # Mock ask_agent to return approve from titan+clawdbot, defer from hermes
    responses = {
        "titan": {"answer": "Yes, approve this fix"},
        "clawdbot": {"answer": "I agree, approve"},
        "hermes": {"answer": "I'm not sure, let me defer"},
    }

    async def mock_ask(from_a, to_a, question, context=None, timeout=20):
        return responses.get(to_a, {"answer": "defer"})

    with patch.object(self_audit, "ask_agent", side_effect=mock_ask):
        result = run(self_audit._discuss_with_agents([finding]))

    assert result[0]["_consensus"] == "approved"
    assert result[0]["_votes"]["titan"] == "approve"
    assert result[0]["_votes"]["clawdbot"] == "approve"
    assert result[0]["_votes"]["hermes"] == "defer"


def test_consensus_rejects_on_any_veto():
    """Any agent rejecting should block the fix."""
    from perseus import self_audit

    finding = {
        "file": "test.py",
        "issue": "test issue",
        "severity": "medium",
        "failure_mode": "dead_code",
        "proposed_fix": "remove function",
        "fix_type": "code_edit",
        "reasoning": "never called",
    }

    responses = {
        "titan": {"answer": "Yes, approve"},
        "clawdbot": {"answer": "I reject this — it will break site builds"},
        "hermes": {"answer": "approve"},
    }

    async def mock_ask(from_a, to_a, question, context=None, timeout=20):
        return responses.get(to_a, {"answer": "defer"})

    with patch.object(self_audit, "ask_agent", side_effect=mock_ask):
        result = run(self_audit._discuss_with_agents([finding]))

    # C-10 fix: 2/3 approve meets CONSENSUS_THRESHOLD=2, so consensus is "approved"
    # (previously a single reject would veto, contradicting the stated threshold)
    assert result[0]["_consensus"] == "approved"


def test_analyze_file_returns_structured_findings():
    from perseus import self_audit

    llm_response = json.dumps({
        "findings": [
            {
                "file": "shared/comms.py",
                "line": 263,
                "issue": "SQL injection in interval string",
                "failure_mode": "schema_mismatch",
                "severity": "high",
                "proposed_fix": "Use make_interval instead of string interpolation",
                "fix_type": "code_edit",
                "reasoning": "Parameterized value inside string literal produces invalid SQL",
            }
        ]
    })

    mock_llm = AsyncMock(return_value=llm_response)
    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        findings = run(self_audit._analyze_file("shared/comms.py", "def is_agent_alive(): ..."))

    assert len(findings) == 1
    assert findings[0]["failure_mode"] == "schema_mismatch"
    assert findings[0]["severity"] == "high"


def test_analyze_immutable_file_sets_no_fix():
    from perseus import self_audit

    llm_response = json.dumps({
        "findings": [
            {
                "file": "orchestrator.py",
                "line": 10,
                "issue": "Some issue",
                "failure_mode": "dead_code",
                "severity": "low",
                "proposed_fix": "delete it",
                "fix_type": "code_edit",
                "reasoning": "not used",
            }
        ]
    })

    mock_llm = AsyncMock(return_value=llm_response)
    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        findings = run(self_audit._analyze_file("orchestrator.py", "class Orchestrator: ..."))

    assert len(findings) == 1
    assert findings[0]["fix_type"] == "no_fix_needed"
    assert findings[0]["_immutable"] is True
