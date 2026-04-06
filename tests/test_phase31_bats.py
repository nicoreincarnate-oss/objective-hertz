"""Phase 31 tests: BATS — Budget-Aware Tool-Use Scaling."""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# UnifiedBudget tests
# ---------------------------------------------------------------------------

class TestUnifiedBudget:
    def test_initial_regime_is_high(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)
        assert b.budget_regime == "HIGH"
        assert b.remaining_pct == 100.0

    def test_regime_transitions(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)

        b.record_token_spend(0.25)  # 75% remaining
        assert b.budget_regime == "HIGH"

        b.record_token_spend(0.10)  # 65% remaining
        assert b.budget_regime == "MEDIUM"

        b.record_token_spend(0.40)  # 25% remaining
        assert b.budget_regime == "LOW"

        b.record_token_spend(0.20)  # 5% remaining
        assert b.budget_regime == "CRITICAL"

    def test_total_budget_includes_tools(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(
            token_budget_usd=1.0,
            tool_budgets={"web_search": 5},
            tool_prices={"web_search": 0.01},
        )
        assert b.total_budget == pytest.approx(1.05)

    def test_total_spent_includes_tools(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(
            token_budget_usd=1.0,
            tool_prices={"web_search": 0.01},
        )
        b.record_token_spend(0.50)
        b.record_tool_use("web_search")
        b.record_tool_use("web_search")
        assert b.total_spent == pytest.approx(0.52)

    def test_record_tool_use_assigns_default_price(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)
        b.record_tool_use("unknown_tool")
        assert "unknown_tool" in b.tool_prices
        assert b.tool_used["unknown_tool"] == 1

    def test_format_status_contains_regime(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)
        status = b.format_status()
        assert "regime=HIGH" in status
        assert "BUDGET:" in status

    def test_regime_hint_per_regime(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)
        assert "explore" in b.regime_hint

        b.token_spent_usd = 0.5
        assert "verify" in b.regime_hint

        b.token_spent_usd = 0.85
        assert "wrap up" in b.regime_hint

        b.token_spent_usd = 0.95
        assert "immediately" in b.regime_hint

    def test_remaining_pct_never_negative(self):
        from shared.cost_events import UnifiedBudget
        b = UnifiedBudget(token_budget_usd=1.0)
        b.record_token_spend(2.0)  # overspend
        assert b.remaining_pct == 0.0


class TestCreateDefaultBudget:
    def test_creates_with_defaults(self):
        from shared.cost_events import create_default_budget
        b = create_default_budget()
        assert b.token_budget_usd == 1.0
        assert len(b.tool_budgets) > 0
        assert len(b.tool_prices) > 0

    def test_custom_budget(self):
        from shared.cost_events import create_default_budget
        b = create_default_budget(token_budget_usd=5.0, tool_budget_per_tool=20)
        assert b.token_budget_usd == 5.0
        for v in b.tool_budgets.values():
            assert v == 20


class TestCostEventEnhancements:
    def test_cost_event_has_new_fields(self):
        from shared.cost_events import CostEvent
        e = CostEvent(
            agent_id="test",
            model="sonnet",
            tool_name="web_search",
            budget_regime="MEDIUM",
        )
        assert e.tool_name == "web_search"
        assert e.budget_regime == "MEDIUM"

    def test_cost_event_defaults_to_none(self):
        from shared.cost_events import CostEvent
        e = CostEvent(agent_id="test", model="sonnet")
        assert e.tool_name is None
        assert e.budget_regime is None
