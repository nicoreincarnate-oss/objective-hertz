"""Phase 30: Governance + Safety Foundation — governance tests.

Tests for:
- AGENT_POLICIES dict structure
- governance_check() function
- Audit trail logging (_log_governance_audit)
- GovernanceViolation exception
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# AGENT_POLICIES structure
# ---------------------------------------------------------------------------


def test_agent_policies_contains_all_agents():
    from shared.middleware import AGENT_POLICIES

    expected = {"titan", "clawdbot", "hermes", "perseus", "ruflo"}
    assert set(AGENT_POLICIES.keys()) == expected


def test_agent_policies_required_keys():
    from shared.middleware import AGENT_POLICIES

    for agent, policy in AGENT_POLICIES.items():
        assert "allowed_targets" in policy, f"{agent} missing allowed_targets"
        assert "max_cost_per_dispatch" in policy, f"{agent} missing max_cost_per_dispatch"
        assert "allowed_capabilities" in policy, f"{agent} missing allowed_capabilities"
        assert isinstance(policy["allowed_targets"], list)
        assert isinstance(policy["max_cost_per_dispatch"], (int, float))
        assert isinstance(policy["allowed_capabilities"], list)


def test_perseus_has_wildcard_access():
    from shared.middleware import AGENT_POLICIES

    assert "*" in AGENT_POLICIES["perseus"]["allowed_capabilities"]


def test_perseus_can_dispatch_to_all_agents():
    from shared.middleware import AGENT_POLICIES

    targets = AGENT_POLICIES["perseus"]["allowed_targets"]
    assert "titan" in targets
    assert "hermes" in targets
    assert "clawdbot" in targets
    assert "ruflo" in targets
    assert "deerflow" in targets


# ---------------------------------------------------------------------------
# governance_check() function
# ---------------------------------------------------------------------------


class TestGovernanceCheck:
    def test_allowed_dispatch(self):
        from shared.middleware import governance_check

        allowed, reason = governance_check("titan", "clawdbot", "build_site", 0.5)
        assert allowed is True
        assert reason == "allowed"

    def test_unknown_source_denied(self):
        from shared.middleware import governance_check

        allowed, reason = governance_check("unknown_agent", "titan", "build_site")
        assert allowed is False
        assert "unknown source agent" in reason

    def test_unauthorized_target_denied(self):
        from shared.middleware import governance_check

        # ClawdBot can only dispatch to titan and hermes
        allowed, reason = governance_check("clawdbot", "perseus", "status_query")
        assert allowed is False
        assert "not allowed to dispatch" in reason

    def test_unauthorized_capability_denied(self):
        from shared.middleware import governance_check

        # Ruflo can only do code_fix, test_run, report
        allowed, reason = governance_check("ruflo", "titan", "build_site")
        assert allowed is False
        assert "not allowed capability" in reason

    def test_cost_exceeded_denied(self):
        from shared.middleware import governance_check

        # Hermes max cost is 0.2
        allowed, reason = governance_check("hermes", "titan", "alert", estimated_cost=0.5)
        assert allowed is False
        assert "exceeds max" in reason

    def test_cost_within_limit_allowed(self):
        from shared.middleware import governance_check

        allowed, reason = governance_check("hermes", "titan", "alert", estimated_cost=0.1)
        assert allowed is True

    def test_wildcard_capability_allows_anything(self):
        from shared.middleware import governance_check

        allowed, reason = governance_check("perseus", "titan", "anything_at_all")
        assert allowed is True

    def test_empty_capability_allowed(self):
        from shared.middleware import governance_check

        allowed, reason = governance_check("titan", "clawdbot", "")
        assert allowed is True

    def test_governance_disabled_allows_all(self):
        from shared.middleware import governance_check

        with patch.dict(os.environ, {"MAS_GOVERNANCE": "false"}):
            allowed, reason = governance_check("unknown", "unknown", "unknown")
            assert allowed is True
            assert "disabled" in reason


# ---------------------------------------------------------------------------
# GovernanceViolation exception
# ---------------------------------------------------------------------------


def test_governance_violation_exception():
    from shared.middleware import GovernanceViolation

    exc = GovernanceViolation("clawdbot", "perseus", "not allowed")
    assert exc.source == "clawdbot"
    assert exc.target == "perseus"
    assert "clawdbot" in str(exc)
    assert "perseus" in str(exc)


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_governance_audit_inserts():
    from shared.middleware import _log_governance_audit

    with patch("shared.db.execute", new_callable=AsyncMock) as mock_exec:
        await _log_governance_audit(
            source_agent="titan",
            target_agent="clawdbot",
            action="build_site",
            protocol="a2a",
            policy_result="allowed",
            cost_estimate=0.5,
            correlation_id="test-123",
            metadata={"test": True},
        )
        mock_exec.assert_called_once()
        args = mock_exec.call_args
        assert "agent_audit_log" in args[0][0]
        params = args[0][1]
        assert params[0] == "test-123"  # correlation_id
        assert params[1] == "titan"  # source_agent
        assert params[2] == "clawdbot"  # target_agent
        assert params[6] == 0.5  # cost_estimate


@pytest.mark.asyncio
async def test_log_governance_audit_handles_db_failure():
    """Audit logging should never raise — fire-and-forget."""
    from shared.middleware import _log_governance_audit

    with patch("shared.db.execute", new_callable=AsyncMock, side_effect=OSError("db down")):
        # Should not raise
        await _log_governance_audit(
            source_agent="titan",
            target_agent="clawdbot",
            action="test",
            protocol="a2a",
            policy_result="allowed",
        )
