"""Phase 31 tests: Integration wiring — BATS<->BACM, BATS<->UGO."""
from __future__ import annotations

import os


class TestBATSxBACMIntegration:
    """BATS budget regime drives BACM compression intensity."""

    def setup_method(self):
        os.environ["BACM_COMPRESSION"] = "true"
        os.environ["BATS_ADAPTIVE_BUDGET"] = "true"

    def teardown_method(self):
        os.environ.pop("BACM_COMPRESSION", None)
        os.environ.pop("BATS_ADAPTIVE_BUDGET", None)

    def test_high_regime_no_compression(self):
        from openjarvis.sessions.compression import BACMCompressor
        from shared.cost_events import UnifiedBudget

        budget = UnifiedBudget(token_budget_usd=1.0, token_spent_usd=0.1)
        assert budget.budget_regime == "HIGH"

        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        c = BACMCompressor()
        # High remaining budget -> high ratio -> no compression
        result = c.compress(msgs, budget_ratio=budget.remaining_pct / 100.0)
        assert len(result) == len(msgs)

    def test_critical_regime_compresses(self):
        from openjarvis.sessions.compression import BACMCompressor
        from shared.cost_events import UnifiedBudget

        budget = UnifiedBudget(token_budget_usd=1.0, token_spent_usd=0.95)
        assert budget.budget_regime == "CRITICAL"

        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(30)]
        msgs.insert(5, {"role": "tool", "content": "result"})
        msgs.insert(15, {"role": "tool", "content": "result"})

        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=budget.remaining_pct / 100.0)
        assert len(result) < len(msgs)


class TestBATSxUGOIntegration:
    """UGO reads BATS budget regime for cost estimation."""

    def setup_method(self):
        os.environ["UGO_UTILITY_ROUTING"] = "true"

    def teardown_method(self):
        os.environ.pop("UGO_UTILITY_ROUTING", None)

    def test_ugo_reads_budget_regime(self):
        from shared.capability_router import score_actions
        from shared.cost_events import UnifiedBudget

        # HIGH budget -> more exploration
        high_budget = UnifiedBudget(token_budget_usd=10.0, token_spent_usd=1.0)
        high_scores = score_actions(current_confidence=0.5, budget=high_budget)

        # CRITICAL budget -> more respond/stop
        low_budget = UnifiedBudget(token_budget_usd=10.0, token_spent_usd=9.5)
        low_scores = score_actions(current_confidence=0.5, budget=low_budget)

        # In CRITICAL regime, respond should score relatively higher
        high_respond = next(s for s in high_scores if s.action == "respond")
        low_respond = next(s for s in low_scores if s.action == "respond")
        assert low_respond.gain >= high_respond.gain  # regime bias adds to respond gain

    def test_ugo_tool_cost_normalized_by_budget(self):
        from shared.capability_router import _estimate_cost
        from shared.cost_events import UnifiedBudget

        small_budget = UnifiedBudget(token_budget_usd=0.10)
        large_budget = UnifiedBudget(token_budget_usd=10.0)

        cost_small = _estimate_cost("tool_call", small_budget)
        cost_large = _estimate_cost("tool_call", large_budget)
        assert cost_small > cost_large  # same tool costs more relative to small budget


class TestLoopGuardBACMWiring:
    """LoopGuard uses BACM when budget is set."""

    def setup_method(self):
        os.environ["BACM_COMPRESSION"] = "true"

    def teardown_method(self):
        os.environ.pop("BACM_COMPRESSION", None)

    def test_loop_guard_has_set_budget(self):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig
        from shared.cost_events import UnifiedBudget

        lg = LoopGuard(LoopGuardConfig())
        budget = UnifiedBudget(token_budget_usd=1.0)
        lg.set_budget(budget)
        assert lg._budget is budget

    def test_loop_guard_bacm_disabled_flag(self):
        os.environ["BACM_COMPRESSION"] = "false"
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        lg = LoopGuard(LoopGuardConfig())
        assert not lg._is_bacm_enabled()
