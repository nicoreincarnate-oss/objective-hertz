"""Regression tests for budget source-of-truth queries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_budget_guard_uses_effective_budget_view():
    code = (ROOT / "tools" / "budget_guard.py").read_text()

    assert "v_effective_budget_tracking" in code
    assert "FROM budget_tracking WHERE month" not in code


def test_llm_budget_gate_uses_effective_budget_view():
    code = (ROOT / "shared" / "llm_client.py").read_text()

    assert "v_effective_budget_tracking" in code
    assert "FROM budget_tracking WHERE month" not in code


def test_schema_seeds_recurring_budget_costs():
    sql = (ROOT / "scripts" / "init-db.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS budget_recurring_costs" in sql
    assert "instantly_subscription" in sql
    assert "CREATE OR REPLACE VIEW v_effective_budget_tracking" in sql
