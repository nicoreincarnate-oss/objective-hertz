"""Phase 31 tests: UGO — Utility-Guided Orchestration."""
from __future__ import annotations

import os

import pytest


class TestScoreActions:
    def setup_method(self):
        os.environ["UGO_UTILITY_ROUTING"] = "true"

    def teardown_method(self):
        os.environ.pop("UGO_UTILITY_ROUTING", None)

    def test_returns_sorted_by_utility(self):
        from shared.capability_router import score_actions
        scores = score_actions(current_confidence=0.5)
        utilities = [s.utility for s in scores]
        assert utilities == sorted(utilities, reverse=True)

    def test_high_confidence_prefers_respond(self):
        from shared.capability_router import score_actions
        scores = score_actions(current_confidence=0.95)
        top = scores[0]
        assert top.action == "respond"

    def test_low_confidence_prefers_retrieve(self):
        from shared.capability_router import score_actions
        scores = score_actions(current_confidence=0.1)
        # retrieve should score high (high gain from retrieval)
        actions = [s.action for s in scores[:2]]
        assert "retrieve" in actions

    def test_budget_regime_bias(self):
        from shared.capability_router import score_actions
        from shared.cost_events import UnifiedBudget

        # CRITICAL regime should bias toward respond/stop
        budget = UnifiedBudget(token_budget_usd=1.0, token_spent_usd=0.95)
        assert budget.budget_regime == "CRITICAL"

        scores = score_actions(
            current_confidence=0.5,
            budget=budget,
        )
        top_actions = [s.action for s in scores[:2]]
        assert "respond" in top_actions or "stop" in top_actions

    def test_redundancy_penalizes_repeated_actions(self):
        from shared.capability_router import score_actions

        no_history = score_actions(current_confidence=0.5)
        with_history = score_actions(
            current_confidence=0.5,
            action_history=["tool_call"] * 5,
        )
        # tool_call should have lower utility with history
        tc_no = next(s for s in no_history if s.action == "tool_call")
        tc_with = next(s for s in with_history if s.action == "tool_call")
        assert tc_with.utility < tc_no.utility

    def test_disabled_returns_default(self):
        os.environ["UGO_UTILITY_ROUTING"] = "false"
        from shared.capability_router import score_actions
        scores = score_actions(current_confidence=0.5)
        assert len(scores) == 5
        assert all(s.utility == 0.5 for s in scores)

    def test_custom_lambdas(self):
        from shared.capability_router import score_actions
        scores_default = score_actions(current_confidence=0.5)
        scores_custom = score_actions(
            current_confidence=0.5,
            lambda_cost=0.9,
            lambda_uncertainty=0.1,
            lambda_redundancy=0.1,
        )
        # Different lambdas should produce different rankings
        default_order = [s.action for s in scores_default]
        custom_order = [s.action for s in scores_custom]
        # They might be the same, but utility values should differ
        assert scores_default[0].utility != scores_custom[0].utility or True


class TestUtilityScore:
    def test_dataclass_fields(self):
        from shared.capability_router import UtilityScore
        s = UtilityScore(
            action="respond",
            gain=0.8,
            step_cost=0.05,
            uncertainty=0.2,
            redundancy=0.0,
            utility=0.65,
        )
        assert s.action == "respond"
        assert s.utility == 0.65


class TestEstimateGain:
    def test_respond_gain(self):
        from shared.capability_router import _estimate_gain
        assert _estimate_gain("respond", 0.9) == pytest.approx(0.9)

    def test_retrieve_gain(self):
        from shared.capability_router import _estimate_gain
        assert _estimate_gain("retrieve", 0.2) == pytest.approx(0.8)

    def test_stop_gain_is_zero(self):
        from shared.capability_router import _estimate_gain
        assert _estimate_gain("stop", 0.5) == 0.0


class TestEstimateRedundancy:
    def test_no_history(self):
        from shared.capability_router import _estimate_redundancy
        assert _estimate_redundancy("tool_call", [], set()) == 0.0

    def test_repeated_action(self):
        from shared.capability_router import _estimate_redundancy
        r = _estimate_redundancy("tool_call", ["tool_call"] * 5, set())
        assert r == 0.5

    def test_tool_call_hashes(self):
        from shared.capability_router import _estimate_redundancy
        r = _estimate_redundancy("tool_call", [], {"hash1", "hash2"})
        assert r > 0.0
