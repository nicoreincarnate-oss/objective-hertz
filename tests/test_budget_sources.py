"""Regression tests for budget source-of-truth queries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_budget_guard_uses_effective_budget_view():
    code = (ROOT / "tools" / "budget_guard.py").read_text()

    assert "v_effective_budget_tracking" in code
    assert "FROM budget_tracking WHERE month" not in code


def test_llm_budget_uses_consolidated_middleware():
    """LLMClient delegates budget enforcement to shared/middleware.py."""
    code = (ROOT / "shared" / "llm_client.py").read_text()
    assert "check_budget_for_llm_call" in code
    # v_effective_budget_tracking lives in middleware now, not llm_client
    mw_code = (ROOT / "shared" / "middleware.py").read_text()
    assert "v_effective_budget_tracking" in mw_code


def test_schema_seeds_recurring_budget_costs():
    sql = (ROOT / "scripts" / "init-db.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS budget_recurring_costs" in sql
    assert "instantly_subscription" in sql
    assert "CREATE OR REPLACE VIEW v_effective_budget_tracking" in sql


def test_middleware_budget_uses_effective_budget_view():
    """Consolidated budget path must query v_effective_budget_tracking view."""
    code = (ROOT / "shared" / "middleware.py").read_text()
    assert "v_effective_budget_tracking" in code


def test_middleware_budget_fails_closed():
    """Consolidated budget path must NOT contain fail-open patterns."""
    code = (ROOT / "shared" / "middleware.py").read_text()
    # The check_budget_for_llm_call function must return "local" on error
    assert 'return "local"' in code
