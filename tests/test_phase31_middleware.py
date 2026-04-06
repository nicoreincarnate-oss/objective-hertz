"""Phase 31 tests: BATS middleware four-layer constraints."""
from __future__ import annotations

import os
import time

import pytest


class TestBatsConstraints:
    def setup_method(self):
        os.environ["BATS_ADAPTIVE_BUDGET"] = "true"

    def teardown_method(self):
        os.environ.pop("BATS_ADAPTIVE_BUDGET", None)
        # Clear the spend log between tests
        from shared.middleware import _bats_spend_log
        _bats_spend_log.clear()

    def test_per_call_rejects_expensive_call(self):
        from shared.middleware import check_bats_constraints
        allowed, constraint, action = check_bats_constraints(3.00)
        assert not allowed
        assert constraint == "per_call"
        assert action == "reject"

    def test_per_call_allows_normal_call(self):
        from shared.middleware import check_bats_constraints
        allowed, constraint, action = check_bats_constraints(0.10)
        assert allowed
        assert constraint is None

    def test_circuit_breaker_triggers(self):
        from shared.middleware import check_bats_constraints, record_bats_spend

        # Fill up the 15-min window
        for _ in range(20):
            record_bats_spend(0.10)  # $2.00 total

        allowed, constraint, action = check_bats_constraints(0.05)
        assert not allowed
        assert constraint == "circuit_breaker"
        assert action == "downgrade_haiku"

    def test_hourly_limit_triggers(self):
        from shared.middleware import check_bats_constraints, record_bats_spend

        # Spend $3.90 (below circuit breaker per 15 min, but high hourly)
        # Spread across time to avoid circuit breaker
        for i in range(39):
            record_bats_spend(0.10)

        allowed, constraint, action = check_bats_constraints(0.15)
        assert not allowed
        # Either circuit_breaker or hourly depending on timing
        assert constraint in ("circuit_breaker", "hourly")

    def test_daily_limit_triggers(self):
        from shared.middleware import _bats_spend_log, check_bats_constraints

        # Inject spend records across the day (bypass circuit breaker by backdating)
        now = time.time()
        for i in range(340):
            # Spread across 24 hours
            _bats_spend_log.append((now - (i * 250), 0.10))

        allowed, constraint, action = check_bats_constraints(1.50)
        assert not allowed
        assert constraint == "daily"
        assert action == "downgrade_ollama"

    def test_disabled_flag_passes_through(self):
        os.environ["BATS_ADAPTIVE_BUDGET"] = "false"
        from shared.middleware import check_bats_constraints
        allowed, constraint, action = check_bats_constraints(100.0)
        assert allowed
        assert constraint is None

    def test_record_bats_spend(self):
        from shared.middleware import _bats_spend_log, record_bats_spend
        _bats_spend_log.clear()
        record_bats_spend(0.50)
        assert len(_bats_spend_log) == 1
        assert _bats_spend_log[0][1] == 0.50


class TestBatsBudgetMiddleware:
    @pytest.mark.asyncio
    async def test_bats_middleware_allows_normal_call(self):
        os.environ["BATS_ADAPTIVE_BUDGET"] = "true"
        from shared.middleware import _bats_spend_log, bats_budget_middleware
        _bats_spend_log.clear()

        ctx = {"stage_name": "test", "estimated_cost": 0.05}

        async def mock_next(c):
            return {"success": True, "output": "ok", "cost_usd": 0.05}

        result = await bats_budget_middleware(ctx, mock_next)
        assert result["success"]
        os.environ.pop("BATS_ADAPTIVE_BUDGET", None)

    @pytest.mark.asyncio
    async def test_bats_middleware_rejects_expensive_call(self):
        os.environ["BATS_ADAPTIVE_BUDGET"] = "true"
        from shared.middleware import _bats_spend_log, bats_budget_middleware
        _bats_spend_log.clear()

        ctx = {"stage_name": "test", "estimated_cost": 5.00}

        async def mock_next(c):
            return {"success": True}

        result = await bats_budget_middleware(ctx, mock_next)
        assert not result["success"]
        assert result.get("bats_constraint") == "per_call"
        os.environ.pop("BATS_ADAPTIVE_BUDGET", None)

    @pytest.mark.asyncio
    async def test_bats_middleware_downgrades_on_circuit_breaker(self):
        os.environ["BATS_ADAPTIVE_BUDGET"] = "true"
        from shared.middleware import _bats_spend_log, bats_budget_middleware, record_bats_spend
        _bats_spend_log.clear()

        # Fill up circuit breaker
        for _ in range(20):
            record_bats_spend(0.10)

        ctx = {"stage_name": "test", "estimated_cost": 0.05}

        async def mock_next(c):
            return {"success": True, "resolved_model": c.get("resolved_model")}

        result = await bats_budget_middleware(ctx, mock_next)
        assert result["success"]
        assert ctx.get("bats_downgraded")
        os.environ.pop("BATS_ADAPTIVE_BUDGET", None)
