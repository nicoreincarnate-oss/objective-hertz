"""Tests for the lead state machine — the core business logic guard."""

import pytest
from titan.state_machine import can_transition, valid_next_states, TRANSITIONS


class TestValidTransitions:
    """Every legal pipeline path should be allowed."""

    def test_happy_path_discovery_to_paid(self):
        """The full revenue pipeline: discovered → ... → paid."""
        path = [
            "discovered", "researched", "email_drafted", "email_queued",
            "email_sent", "followed_up", "replied", "interested",
            "demo_built", "proposal_sent", "closed",
            "building", "deployed", "invoiced", "paid",
        ]
        for i in range(len(path) - 1):
            assert can_transition(path[i], path[i + 1]), \
                f"{path[i]} → {path[i+1]} should be valid"

    def test_interested_cannot_skip_demo_before_proposal(self):
        """The strategy requires a demo before a proposal can be sent."""
        assert not can_transition("interested", "proposal_sent")

    def test_proposal_to_closed(self):
        assert can_transition("proposal_sent", "closed")

    def test_negotiating_to_closed(self):
        assert can_transition("negotiating", "closed")

    def test_unresponsive_can_retry(self):
        """Unresponsive leads can re-enter the pipeline."""
        assert can_transition("unresponsive", "email_drafted")


class TestInvalidTransitions:
    """Illegal state jumps must be blocked."""

    def test_cannot_skip_research(self):
        assert not can_transition("discovered", "email_drafted")

    def test_cannot_go_backward(self):
        assert not can_transition("email_sent", "discovered")
        assert not can_transition("paid", "invoiced")
        assert not can_transition("deployed", "closed")

    def test_cannot_skip_to_paid(self):
        assert not can_transition("closed", "paid")
        assert not can_transition("proposal_sent", "paid")

    def test_terminal_states_have_no_exits(self):
        assert valid_next_states("paid") == []
        assert valid_next_states("lost") == []
        assert valid_next_states("unsubscribed") == []

    def test_unknown_status_blocked(self):
        assert not can_transition("nonexistent", "discovered")
        assert valid_next_states("nonexistent") == []


class TestLostTransitions:
    """Every non-terminal state should be able to reach 'lost'."""

    @pytest.mark.parametrize("status", [
        s for s, targets in TRANSITIONS.items()
        if targets and s not in ("unresponsive",)
    ])
    def test_can_mark_lost(self, status):
        # Every active state should allow marking a lead as lost
        # (except unresponsive which goes back to email_drafted)
        if "lost" in TRANSITIONS[status]:
            assert can_transition(status, "lost")
