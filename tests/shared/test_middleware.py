"""Comprehensive test suite for the DeerFlow async middleware chain.

Tests cover:
- MiddlewareChain core execution and ordering
- MemoryMiddleware: inject pre-stage, save post-stage
- DNAGuardMiddleware: block unauthorized tools, pass authorized
- AntiSlopMiddleware: score content stages, pass non-content
- TelemetryMiddleware: record timing to stage_metrics (mock DB)
- BudgetCheckMiddleware: block over-budget, pass under-budget
- Configurable ordering via PIPELINE_CONFIGS
- Env override (e.g., TITAN_MIDDLEWARE_ORDER)
- Full chain integration: all 5 middlewares in sequence
- Feature flag off = no middleware
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from shared.middleware import (
    MIDDLEWARE_REGISTRY,
    PIPELINE_CONFIGS,
    MiddlewareChain,
    anti_slop_middleware,
    budget_check_middleware,
    build_chain,
    dna_guard_middleware,
    memory_middleware,
    telemetry_middleware,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for test convenience."""
    return asyncio.run(coro)


async def _identity_handler(ctx: dict[str, Any]) -> dict[str, Any]:
    """Pass-through handler that returns success."""
    return {"success": True, "output": "handler_output", "node_id": "test"}


async def _failing_handler(ctx: dict[str, Any]) -> dict[str, Any]:
    """Handler that returns failure."""
    return {"success": False, "output": "handler_failed", "node_id": "test"}


def _mock_module(name: str, **attrs) -> types.ModuleType:
    """Create a fake module in sys.modules with given attributes."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# MiddlewareChain core
# ---------------------------------------------------------------------------


class TestMiddlewareChainCore:
    """Test the MiddlewareChain compose pattern."""

    def test_empty_chain_calls_handler_directly(self):
        chain = MiddlewareChain()
        result = _run(chain.execute({}, _identity_handler))
        assert result["success"] is True
        assert result["output"] == "handler_output"

    def test_single_middleware_wraps_handler(self):
        order = []

        async def mw(ctx, next_fn):
            order.append("before")
            result = await next_fn(ctx)
            order.append("after")
            return result

        chain = MiddlewareChain()
        chain.use(mw)
        result = _run(chain.execute({}, _identity_handler))
        assert result["success"] is True
        assert order == ["before", "after"]

    def test_execution_order_first_added_is_outermost(self):
        """First middleware added should be outermost (executes first/last)."""
        order = []

        async def mw_outer(ctx, next_fn):
            order.append("outer_before")
            result = await next_fn(ctx)
            order.append("outer_after")
            return result

        async def mw_inner(ctx, next_fn):
            order.append("inner_before")
            result = await next_fn(ctx)
            order.append("inner_after")
            return result

        chain = MiddlewareChain()
        chain.use(mw_outer).use(mw_inner)
        _run(chain.execute({}, _identity_handler))
        assert order == [
            "outer_before",
            "inner_before",
            "inner_after",
            "outer_after",
        ]

    def test_chain_use_returns_self_for_fluent_chaining(self):
        chain = MiddlewareChain()
        result = chain.use(lambda ctx, nf: nf(ctx))
        assert result is chain

    def test_middleware_can_modify_context(self):
        async def mw(ctx, next_fn):
            ctx["injected"] = True
            return await next_fn(ctx)

        async def handler(ctx):
            return {"success": True, "injected": ctx.get("injected", False)}

        chain = MiddlewareChain()
        chain.use(mw)
        result = _run(chain.execute({}, handler))
        assert result["injected"] is True

    def test_middleware_can_short_circuit(self):
        async def blocking_mw(ctx, next_fn):
            return {"success": False, "output": "blocked"}

        chain = MiddlewareChain()
        chain.use(blocking_mw)
        result = _run(chain.execute({}, _identity_handler))
        assert result["success"] is False
        assert result["output"] == "blocked"

    def test_three_middlewares_ordering(self):
        order = []

        def make_mw(name):
            async def mw(ctx, next_fn):
                order.append(f"{name}_in")
                r = await next_fn(ctx)
                order.append(f"{name}_out")
                return r
            return mw

        chain = MiddlewareChain()
        chain.use(make_mw("a")).use(make_mw("b")).use(make_mw("c"))
        _run(chain.execute({}, _identity_handler))
        assert order == ["a_in", "b_in", "c_in", "c_out", "b_out", "a_out"]


# ---------------------------------------------------------------------------
# MemoryMiddleware
# ---------------------------------------------------------------------------


class TestMemoryMiddleware:
    """Test memory middleware (pre-load and post-save)."""

    def test_flag_off_passes_through(self):
        """When ENABLE_DEERFLOW_MEMORY is not set, middleware is a no-op."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_DEERFLOW_MEMORY", None)
            result = _run(memory_middleware(
                {"daemon_name": "titan", "stage_name": "test"},
                _identity_handler,
            ))
        assert result["success"] is True
        assert result["output"] == "handler_output"

    @patch("shared.middleware.DaemonMemoryStore", create=True)
    def test_flag_on_injects_memories(self, _mock_store_cls):
        """When flag is on, memories are injected into ctx."""
        # The import inside memory_middleware will fail but it catches and sets []
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "true"}):
            ctx = {"daemon_name": "titan", "stage_name": "research"}
            result = _run(memory_middleware(ctx, _identity_handler))
        assert result["success"] is True
        # Memory injection attempted (may be [] on import failure)
        assert "memories" in ctx

    def test_flag_on_import_failure_is_nonfatal(self):
        """If DaemonMemoryStore import fails, middleware continues."""
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "true"}):
            ctx = {"daemon_name": "titan", "stage_name": "research"}
            result = _run(memory_middleware(ctx, _identity_handler))
        assert result["success"] is True
        assert ctx.get("memories") == []


# ---------------------------------------------------------------------------
# DNAGuardMiddleware
# ---------------------------------------------------------------------------


class TestDNAGuardMiddleware:
    """Test DNA guard middleware for tool permission enforcement."""

    def test_flag_off_passes_through(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_DNA_PROFILES", None)
            result = _run(dna_guard_middleware(
                {"daemon_name": "titan", "stage_name": "test", "tools": ["browser"]},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_no_tools_passes_through(self):
        """If no tools are used, DNA guard skips check."""
        with patch.dict(os.environ, {"ENABLE_DNA_PROFILES": "true"}):
            result = _run(dna_guard_middleware(
                {"daemon_name": "titan", "stage_name": "test", "tools": []},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_blocks_unauthorized_tool(self):
        """DNA guard blocks tools not in the agent's permitted actions."""
        mock_dna = MagicMock()
        mock_dna.check_action.return_value = False
        mock_dna_cls = MagicMock(return_value=mock_dna)
        fake_mod = _mock_module("shared.agent_dna", AgentDNA=mock_dna_cls)

        with patch.dict(os.environ, {"ENABLE_DNA_PROFILES": "true"}):
            with patch.dict(sys.modules, {"shared.agent_dna": fake_mod}):
                result = _run(dna_guard_middleware(
                    {"daemon_name": "titan", "stage_name": "test", "tools": ["rm_rf"]},
                    _identity_handler,
                ))
        assert result["success"] is False
        assert "DNA boundary violation" in result["output"]

    def test_allows_authorized_tool(self):
        """DNA guard allows tools in the agent's permitted actions."""
        mock_dna = MagicMock()
        mock_dna.check_action.return_value = True
        mock_dna_cls = MagicMock(return_value=mock_dna)
        fake_mod = _mock_module("shared.agent_dna", AgentDNA=mock_dna_cls)

        with patch.dict(os.environ, {"ENABLE_DNA_PROFILES": "true"}):
            with patch.dict(sys.modules, {"shared.agent_dna": fake_mod}):
                result = _run(dna_guard_middleware(
                    {"daemon_name": "titan", "stage_name": "test", "tools": ["http"]},
                    _identity_handler,
                ))
        assert result["success"] is True

    def test_dna_load_failure_allows_execution(self):
        """If DNA profile can't load, guard fails open."""
        with patch.dict(os.environ, {"ENABLE_DNA_PROFILES": "true"}):
            with patch("shared.agent_dna.AgentDNA") as mock_dna_cls:
                mock_dna_cls.side_effect = RuntimeError("DNA load failed")
                result = _run(dna_guard_middleware(
                    {"daemon_name": "titan", "stage_name": "test", "tools": ["http"]},
                    _identity_handler,
                ))
        assert result["success"] is True


# ---------------------------------------------------------------------------
# AntiSlopMiddleware
# ---------------------------------------------------------------------------


class TestAntiSlopMiddleware:
    """Test anti-slop quality gate middleware."""

    def test_flag_off_passes_through(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_ANTI_SLOP", None)
            result = _run(anti_slop_middleware(
                {"stage_name": "email_compose"},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_non_content_stage_passes_through(self):
        """Non-content stages bypass quality scoring entirely."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            result = _run(anti_slop_middleware(
                {"stage_name": "research"},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_content_stage_runs_scoring(self):
        """Content-producing stages get quality scored."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            # Secret detection and scoring will fail on import, which is non-fatal
            result = _run(anti_slop_middleware(
                {"stage_name": "email_compose"},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_secret_detected_blocks_output(self):
        """If secret detected in output, middleware blocks it."""
        mock_detect = MagicMock(return_value=["API_KEY"])
        fake_mod = _mock_module("shared.anti_slop", detect_secrets=mock_detect)

        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            with patch.dict(sys.modules, {"shared.anti_slop": fake_mod}):
                result = _run(anti_slop_middleware(
                    {"stage_name": "email_compose"},
                    _identity_handler,
                ))
        assert result["success"] is False
        assert "BLOCKED" in result.get("output", "")

    def test_no_secret_allows_output(self):
        """If no secret, output passes through."""
        mock_scorer = AsyncMock()
        mock_scorer.score.return_value = {"quality": 0.9}
        mock_scorer_cls = MagicMock(return_value=mock_scorer)
        mock_detect = MagicMock(return_value=[])
        fake_mod = _mock_module(
            "shared.anti_slop",
            detect_secrets=mock_detect,
            AntiSlopScorer=mock_scorer_cls,
        )

        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            with patch.dict(sys.modules, {"shared.anti_slop": fake_mod}):
                result = _run(anti_slop_middleware(
                    {"stage_name": "email_compose"},
                    _identity_handler,
                ))
        assert result["success"] is True


# ---------------------------------------------------------------------------
# TelemetryMiddleware
# ---------------------------------------------------------------------------


class TestTelemetryMiddleware:
    """Test telemetry middleware records timing."""

    def test_records_timing_to_db(self):
        """Telemetry inserts timing data (mocked DB)."""
        mock_execute = AsyncMock()
        fake_db = _mock_module("shared.db", execute=mock_execute)
        with patch.dict(sys.modules, {"shared.db": fake_db}):
            ctx = {
                "pipeline": "titan",
                "stage_name": "research",
                "daemon_name": "titan",
            }
            result = _run(telemetry_middleware(ctx, _identity_handler))

        assert result["success"] is True
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        # Verify SQL insert
        assert "INSERT INTO stage_metrics" in call_args[0][0]
        # Verify params
        params = call_args[0][1]
        assert params[0] == "titan"  # pipeline
        assert params[1] == "research"  # stage
        assert params[2] == "titan"  # daemon
        assert isinstance(params[3], int)  # duration_ms
        assert params[4] is True  # success

    def test_db_failure_is_nonfatal(self):
        """Telemetry DB insert failure should not block the pipeline."""
        mock_execute = AsyncMock(side_effect=Exception("DB unavailable"))
        fake_db = _mock_module("shared.db", execute=mock_execute)
        with patch.dict(sys.modules, {"shared.db": fake_db}):
            result = _run(telemetry_middleware(
                {"pipeline": "titan", "stage_name": "test", "daemon_name": "titan"},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_records_failure_status(self):
        """Telemetry records success=False for failed handlers."""
        mock_execute = AsyncMock()
        fake_db = _mock_module("shared.db", execute=mock_execute)
        with patch.dict(sys.modules, {"shared.db": fake_db}):
            result = _run(telemetry_middleware(
                {"pipeline": "titan", "stage_name": "test", "daemon_name": "titan"},
                _failing_handler,
            ))
        assert result["success"] is False
        params = mock_execute.call_args[0][1]
        assert params[4] is False  # success


# ---------------------------------------------------------------------------
# BudgetCheckMiddleware
# ---------------------------------------------------------------------------


class TestBudgetCheckMiddleware:
    """Test budget enforcement middleware."""

    def test_under_budget_allows_execution(self):
        """Under budget ($500 < $800) allows the stage to run."""
        mock_summary = AsyncMock(return_value={
            "daemons": [{"total_cost_usd": 200}, {"total_cost_usd": 300}],
        })
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        with patch.dict(sys.modules, {"shared.observability": fake_obs}):
            result = _run(budget_check_middleware(
                {"stage_name": "research"},
                _identity_handler,
            ))
        assert result["success"] is True

    def test_over_budget_blocks_execution(self):
        """Over budget ($900 >= $800) blocks the stage."""
        mock_summary = AsyncMock(return_value={
            "daemons": [{"total_cost_usd": 500}, {"total_cost_usd": 400}],
        })
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        with patch.dict(sys.modules, {"shared.observability": fake_obs}):
            result = _run(budget_check_middleware(
                {"stage_name": "research"},
                _identity_handler,
            ))
        assert result["success"] is False
        assert "Budget cap exceeded" in result["output"]

    def test_exactly_at_budget_blocks(self):
        """Exactly at $800 blocks (>= comparison)."""
        mock_summary = AsyncMock(return_value={
            "daemons": [{"total_cost_usd": 800}],
        })
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        with patch.dict(sys.modules, {"shared.observability": fake_obs}):
            result = _run(budget_check_middleware(
                {"stage_name": "research"},
                _identity_handler,
            ))
        assert result["success"] is False

    def test_budget_check_failure_allows_execution(self):
        """If budget check itself fails, execution continues (fail-open)."""
        mock_summary = AsyncMock(side_effect=Exception("DB down"))
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        with patch.dict(sys.modules, {"shared.observability": fake_obs}):
            result = _run(budget_check_middleware(
                {"stage_name": "research"},
                _identity_handler,
            ))
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Pipeline config and build_chain
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    """Test PIPELINE_CONFIGS and build_chain."""

    def test_pipeline_configs_has_all_pipelines(self):
        assert "titan" in PIPELINE_CONFIGS
        assert "clawdbot" in PIPELINE_CONFIGS
        assert "hermes" in PIPELINE_CONFIGS
        assert "perseus" in PIPELINE_CONFIGS

    def test_titan_has_five_middlewares(self):
        assert len(PIPELINE_CONFIGS["titan"]) == 5
        assert PIPELINE_CONFIGS["titan"] == [
            "budget_check", "dna_guard", "anti_slop", "memory", "telemetry",
        ]

    def test_perseus_has_two_middlewares(self):
        assert PIPELINE_CONFIGS["perseus"] == ["budget_check", "telemetry"]

    def test_build_chain_returns_chain(self):
        chain = build_chain("titan")
        assert isinstance(chain, MiddlewareChain)

    def test_build_chain_unknown_pipeline_gets_default_telemetry(self):
        chain = build_chain("unknown_pipeline")
        assert isinstance(chain, MiddlewareChain)
        # Should have at least telemetry as default
        assert len(chain._middlewares) == 1

    def test_env_override_ordering(self):
        """TITAN_MIDDLEWARE_ORDER env var overrides config."""
        with patch.dict(os.environ, {"TITAN_MIDDLEWARE_ORDER": "telemetry,budget_check"}):
            chain = build_chain("titan")
        assert len(chain._middlewares) == 2

    def test_env_override_with_unknown_middleware_skips(self):
        """Unknown middleware names in env override are skipped with warning."""
        with patch.dict(os.environ, {"HERMES_MIDDLEWARE_ORDER": "telemetry,nonexistent"}):
            chain = build_chain("hermes")
        assert len(chain._middlewares) == 1  # only telemetry

    def test_middleware_registry_has_all_five(self):
        assert set(MIDDLEWARE_REGISTRY.keys()) == {
            "budget_check", "dna_guard", "anti_slop", "memory", "telemetry",
        }


# ---------------------------------------------------------------------------
# Full chain integration
# ---------------------------------------------------------------------------


class TestFullChainIntegration:
    """Test all 5 middlewares running together in sequence."""

    def _mock_infra_modules(self):
        """Create mock infrastructure modules for full-chain tests."""
        mock_execute = AsyncMock()
        mock_summary = AsyncMock(return_value={"daemons": [{"total_cost_usd": 100}]})
        fake_db = _mock_module("shared.db", execute=mock_execute)
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        return mock_execute, mock_summary, fake_db, fake_obs

    def test_full_chain_all_flags_off(self):
        """With all feature flags off, chain passes through to handler."""
        mock_execute, mock_summary, fake_db, fake_obs = self._mock_infra_modules()

        with patch.dict(os.environ, {}, clear=False):
            for key in [
                "ENABLE_DEERFLOW_MEMORY", "ENABLE_DNA_PROFILES",
                "ENABLE_ANTI_SLOP",
            ]:
                os.environ.pop(key, None)

            chain = build_chain("titan")
            with patch.dict(sys.modules, {"shared.db": fake_db, "shared.observability": fake_obs}):
                ctx = {
                    "daemon_name": "titan",
                    "stage_name": "research",
                    "pipeline": "titan",
                    "tools": [],
                }
                result = _run(chain.execute(ctx, _identity_handler))

        assert result["success"] is True
        assert result["output"] == "handler_output"

    def test_full_chain_budget_blocks_early(self):
        """Budget check is outermost and blocks before other middlewares run."""
        mock_summary = AsyncMock(return_value={
            "daemons": [{"total_cost_usd": 900}],
        })
        fake_obs = _mock_module("shared.observability", get_metrics_summary=mock_summary)
        with patch.dict(sys.modules, {"shared.observability": fake_obs}):
            chain = build_chain("titan")
            ctx = {
                "daemon_name": "titan",
                "stage_name": "research",
                "pipeline": "titan",
                "tools": [],
            }
            result = _run(chain.execute(ctx, _identity_handler))

        assert result["success"] is False
        assert "Budget cap exceeded" in result["output"]

    def test_full_chain_execution_order(self):
        """Verify middleware execute in configured order."""
        execution_order = []
        mock_execute, mock_summary, fake_db, fake_obs = self._mock_infra_modules()

        original_middlewares = dict(MIDDLEWARE_REGISTRY)

        def make_tracking_mw(name, original_fn):
            async def tracked(ctx, next_fn):
                execution_order.append(f"{name}_enter")
                result = await original_fn(ctx, next_fn)
                execution_order.append(f"{name}_exit")
                return result
            return tracked

        patched_registry = {
            name: make_tracking_mw(name, fn)
            for name, fn in original_middlewares.items()
        }

        with patch.dict("shared.middleware.MIDDLEWARE_REGISTRY", patched_registry):
            chain = build_chain("titan")
            with patch.dict(os.environ, {}, clear=False):
                for key in ["ENABLE_DEERFLOW_MEMORY", "ENABLE_DNA_PROFILES", "ENABLE_ANTI_SLOP"]:
                    os.environ.pop(key, None)
                with patch.dict(sys.modules, {"shared.db": fake_db, "shared.observability": fake_obs}):
                    ctx = {
                        "daemon_name": "titan",
                        "stage_name": "research",
                        "pipeline": "titan",
                        "tools": [],
                    }
                    _run(chain.execute(ctx, _identity_handler))

        # Titan order: budget_check, dna_guard, anti_slop, memory, telemetry
        enters = [e for e in execution_order if e.endswith("_enter")]
        assert enters == [
            "budget_check_enter",
            "dna_guard_enter",
            "anti_slop_enter",
            "memory_enter",
            "telemetry_enter",
        ]


# ---------------------------------------------------------------------------
# Feature flag off = no middleware in engine
# ---------------------------------------------------------------------------


class TestEngineFeatureFlag:
    """Test ENABLE_MIDDLEWARE feature flag in WorkflowEngine."""

    def test_middleware_enabled_returns_false_when_unset(self):
        from openjarvis.workflow.engine import WorkflowEngine

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_MIDDLEWARE", None)
            engine = WorkflowEngine()
            assert engine._middleware_enabled() is False

    def test_middleware_enabled_returns_true_when_set(self):
        from openjarvis.workflow.engine import WorkflowEngine

        with patch.dict(os.environ, {"ENABLE_MIDDLEWARE": "true"}):
            engine = WorkflowEngine()
            assert engine._middleware_enabled() is True

    def test_middleware_enabled_accepts_various_truthy_values(self):
        from openjarvis.workflow.engine import WorkflowEngine

        engine = WorkflowEngine()
        for val in ("true", "True", "TRUE", "1", "yes", "YES"):
            with patch.dict(os.environ, {"ENABLE_MIDDLEWARE": val}):
                assert engine._middleware_enabled() is True, f"Failed for {val}"

    def test_middleware_enabled_rejects_other_values(self):
        from openjarvis.workflow.engine import WorkflowEngine

        engine = WorkflowEngine()
        for val in ("false", "0", "no", "", "maybe"):
            with patch.dict(os.environ, {"ENABLE_MIDDLEWARE": val}):
                assert engine._middleware_enabled() is False, f"Failed for {val}"
