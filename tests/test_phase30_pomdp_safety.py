"""Phase 30: POMDP Safety Bounds + Memory Poisoning Defense tests.

Tests for:
- RetrievalBeliefState dataclass
- score_anomaly() function
- pomdp_retrieve() wrapper
- _record_retrieval_session() audit logging
- score_action_risk() safety risk scorer
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# RetrievalBeliefState
# ---------------------------------------------------------------------------


def test_belief_state_defaults():
    from shared.magma import RetrievalBeliefState

    bs = RetrievalBeliefState(query="test query")
    assert bs.query == "test query"
    assert bs.retrieved_ids == []
    assert bs.confidence == 0.0
    assert bs.uncertainty_sources == []
    assert bs.retrieval_history == []
    assert bs.poisoning_score == 0.0
    assert bs.step_count == 0


def test_belief_state_custom_values():
    from shared.magma import RetrievalBeliefState

    bs = RetrievalBeliefState(
        query="find leads",
        confidence=0.8,
        poisoning_score=0.3,
        step_count=2,
    )
    assert bs.confidence == 0.8
    assert bs.poisoning_score == 0.3
    assert bs.step_count == 2


# ---------------------------------------------------------------------------
# score_anomaly()
# ---------------------------------------------------------------------------


class TestScoreAnomaly:
    def test_empty_nodes_returns_zero(self):
        from shared.magma import score_anomaly

        assert score_anomaly([]) == 0.0

    def test_no_anomaly_returns_zero(self):
        from shared.magma import score_anomaly

        # Old nodes with established links, diverse sources
        nodes = [
            {"source": "graph", "confidence": 0.7, "link_count": 5,
             "created_at": "2025-01-01T00:00:00+00:00"},
            {"source": "qdrant", "confidence": 0.6, "link_count": 3,
             "created_at": "2025-01-02T00:00:00+00:00"},
            {"source": "zep", "confidence": 0.8, "link_count": 2,
             "created_at": "2025-01-03T00:00:00+00:00"},
        ]
        score = score_anomaly(nodes)
        assert score == 0.0

    def test_freshness_anomaly(self):
        from shared.magma import score_anomaly

        now_iso = datetime.now(timezone.utc).isoformat()
        nodes = [
            {"source": "graph", "created_at": now_iso, "link_count": 0},
        ]
        score = score_anomaly(nodes)
        assert score >= 0.3  # freshness signal

    def test_source_concentration_anomaly(self):
        from shared.magma import score_anomaly

        # All from same source
        nodes = [
            {"source": "graph", "created_at": "2025-01-01T00:00:00+00:00", "link_count": 5}
            for _ in range(5)
        ]
        score = score_anomaly(nodes)
        assert score >= 0.2

    def test_bulk_injection_anomaly(self):
        from shared.magma import score_anomaly

        # 6 nodes created in the same minute
        same_ts = datetime.now(timezone.utc).isoformat()
        nodes = [
            {"source": f"src_{i}", "created_at": same_ts, "link_count": 3}
            for i in range(6)
        ]
        score = score_anomaly(nodes)
        assert score >= 0.3

    def test_semantic_contradiction_anomaly(self):
        from shared.magma import score_anomaly

        nodes = [
            {"source": "graph", "confidence": 0.9, "entity": "acme_corp",
             "created_at": "2025-01-01T00:00:00+00:00", "link_count": 5},
            {"source": "qdrant", "confidence": 0.1, "entity": "acme_corp",
             "created_at": "2025-01-02T00:00:00+00:00", "link_count": 2},
        ]
        score = score_anomaly(nodes)
        assert score >= 0.4

    def test_score_capped_at_one(self):
        from shared.magma import score_anomaly

        # All anomalies at once
        now_iso = datetime.now(timezone.utc).isoformat()
        nodes = [
            {"source": "graph", "created_at": now_iso, "link_count": 0,
             "confidence": 0.9, "entity": "test"}
        ] * 7 + [
            {"source": "graph", "created_at": now_iso, "link_count": 0,
             "confidence": 0.1, "entity": "test"}
        ]
        score = score_anomaly(nodes)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# score_action_risk()
# ---------------------------------------------------------------------------


class TestScoreActionRisk:
    def test_mutate_code_always_blocked(self):
        from shared.magma import score_action_risk

        assert score_action_risk("mutate_code", "anything.py", 10, {}) == 1.0

    def test_mutate_config_budget_key_high_risk(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_config", "budget_monthly_cap", 5, {})
        assert score >= 0.9

    def test_mutate_config_security_key_high_risk(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_config", "api_key_rotation", 5, {})
        assert score >= 0.9

    def test_mutate_config_normal_key_moderate_risk(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_config", "log_level", 5, {})
        assert 0.2 < score < 0.5

    def test_mutate_prompt_small_delta(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_prompt", "titan/pipeline/email_compose.py", 20, {})
        assert score == pytest.approx(0.1 + 20 * 0.005, abs=0.01)

    def test_mutate_prompt_large_delta(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_prompt", "titan/pipeline/email_compose.py", 100, {})
        expected = 0.6 + (100 - 50) * 0.005
        assert score == pytest.approx(expected, abs=0.01)

    def test_protected_path_bonus(self):
        from shared.magma import score_action_risk

        # wallet.py is protected — should add 0.5
        score = score_action_risk("mutate_prompt", "conway/wallet.py", 10, {})
        base = 0.1 + 10 * 0.005
        assert score == pytest.approx(min(1.0, base + 0.5), abs=0.01)

    def test_protected_security_path(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_prompt", "openjarvis/security/caps.py", 10, {})
        assert score >= 0.5

    def test_result_capped_at_one(self):
        from shared.magma import score_action_risk

        # Large delta + protected path
        score = score_action_risk("mutate_prompt", "conway/wallet.py", 500, {})
        assert score <= 1.0

    def test_result_not_negative(self):
        from shared.magma import score_action_risk

        score = score_action_risk("mutate_prompt", "some_file.py", 0, {})
        assert score >= 0.0


# ---------------------------------------------------------------------------
# pomdp_retrieve()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pomdp_retrieve_disabled_bypasses():
    from shared.magma import pomdp_retrieve

    with patch.dict(os.environ, {"POMDP_SAFETY_BOUNDS": "false"}):
        with patch("shared.magma.magma_retrieve", new_callable=AsyncMock, return_value="test result"):
            result, belief = await pomdp_retrieve("test query")
            assert result == "test result"
            assert belief.step_count == 1
            assert belief.confidence == 1.0


@pytest.mark.asyncio
async def test_pomdp_retrieve_enabled_runs_loop():
    from shared.magma import pomdp_retrieve

    with patch.dict(os.environ, {"POMDP_SAFETY_BOUNDS": "true"}):
        with patch("shared.magma.magma_retrieve", new_callable=AsyncMock, return_value="memory content here"):
            with patch("shared.magma._record_retrieval_session"):
                result, belief = await pomdp_retrieve("test query")
                assert belief.step_count >= 1
                assert result != ""


@pytest.mark.asyncio
async def test_pomdp_retrieve_empty_result():
    from shared.magma import pomdp_retrieve

    with patch.dict(os.environ, {"POMDP_SAFETY_BOUNDS": "true"}):
        with patch("shared.magma.magma_retrieve", new_callable=AsyncMock, return_value=""):
            with patch("shared.magma._record_retrieval_session"):
                result, belief = await pomdp_retrieve("no results query")
                assert result == ""
                assert "empty" in belief.uncertainty_sources[0]
