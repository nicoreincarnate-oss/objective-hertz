"""Tests for RLM (Recursive Language Model) composer and email pipeline integration.

Covers:
- RLMComposer recursive compose returns highest-scoring version
- Budget cap stops iteration and returns best draft
- Shadow mode logs comparison but returns original
- Qdrant retrieval returns relevant context (mock)
- Mem0 stores research context (mock)
- Feature flag off = original compose
- Feature flag on, shadow = both paths run
- Feature flag on, no shadow = RLM only
- Context expansion triggers on low specificity
- Monthly spend cap fallback to single-pass
- Runtime toggle via system_config
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# RLMComposer unit tests
# ---------------------------------------------------------------------------


class TestRLMComposerRecursive:
    """RLMComposer.compose() recursive loop behavior."""

    @pytest.mark.asyncio
    async def test_returns_highest_scoring_version(self):
        """Compose should return the version with the best composite score,
        not necessarily the latest iteration."""
        # Mock anti_slop imports used by rlm_composer
        with patch("titan.pipeline.rlm_composer._check_budget", new_callable=AsyncMock) as mock_budget, \
             patch("titan.pipeline.rlm_composer._retrieve_context", new_callable=AsyncMock) as mock_ctx, \
             patch("titan.pipeline.rlm_composer._generate_draft", new_callable=AsyncMock) as mock_draft, \
             patch("titan.pipeline.rlm_composer._expand_context", new_callable=AsyncMock) as mock_expand:

            mock_budget.return_value = (True, "ok")
            mock_ctx.return_value = ["some context"]
            mock_expand.return_value = ["more context"]

            # Draft 0: good JSON, scores will be set by scorer mock
            # Draft 1: good JSON, better scores
            # Draft 2: good JSON, worse scores
            drafts = [
                ('{"subject": "Draft 0", "body": "First attempt body text here."}', 0.005),
                ('{"subject": "Draft 1", "body": "Better second attempt body."}', 0.005),
                ('{"subject": "Draft 2", "body": "Worse third attempt body text."}', 0.005),
            ]
            mock_draft.side_effect = drafts

            # Scores: iteration 1 has highest composite
            scores_sequence = [
                {"clarity": 0.5, "specificity": 0.4, "authenticity": 0.5, "value_density": 0.5, "slop_score": 0.3},
                {"clarity": 0.9, "specificity": 0.9, "authenticity": 0.9, "value_density": 0.9, "slop_score": 0.05},
                {"clarity": 0.6, "specificity": 0.5, "authenticity": 0.6, "value_density": 0.5, "slop_score": 0.2},
            ]

            with patch("titan.pipeline.rlm_composer.AntiSlopScorer") as MockScorer:
                scorer_instance = AsyncMock()
                scorer_instance.score = AsyncMock(side_effect=scores_sequence)
                MockScorer.return_value = scorer_instance

                # _is_good_enough returns False for all (so all 3 iterations run)
                with patch("titan.pipeline.rlm_composer._is_good_enough", return_value=False):
                    from titan.pipeline.rlm_composer import RLMComposer
                    composer = RLMComposer.__new__(RLMComposer)
                    composer._scorer = scorer_instance

                    result = await composer.compose(
                        lead={"id": 1, "business_name": "Test Corp", "lead_score": 50},
                        research={"research_summary": "Test research"},
                    )

            # Should return Draft 1 (highest composite score)
            assert result["subject"] == "Draft 1"
            assert result["body"] == "Better second attempt body."
            assert result["iterations"] == 3

    @pytest.mark.asyncio
    async def test_budget_cap_stops_iteration(self):
        """When per-email budget is exceeded, compose stops and returns best so far."""
        with patch("titan.pipeline.rlm_composer._retrieve_context", new_callable=AsyncMock) as mock_ctx, \
             patch("titan.pipeline.rlm_composer._generate_draft", new_callable=AsyncMock) as mock_draft, \
             patch("titan.pipeline.rlm_composer._check_budget", new_callable=AsyncMock) as mock_budget:

            mock_ctx.return_value = []
            # Budget ok on first call, exceeded on second
            mock_budget.side_effect = [
                (True, "ok"),   # Pre-flight check
                (True, "ok"),   # Iteration 0
                (False, "per-email cap reached ($0.08 >= $0.08)"),  # Iteration 1 blocked
            ]
            mock_draft.return_value = ('{"subject": "Test", "body": "Budget test body content."}', 0.05)

            scores = {"clarity": 0.7, "specificity": 0.7, "authenticity": 0.7, "value_density": 0.7, "slop_score": 0.1}

            with patch("titan.pipeline.rlm_composer._is_good_enough", return_value=False):
                from titan.pipeline.rlm_composer import RLMComposer
                composer = RLMComposer.__new__(RLMComposer)
                scorer_mock = AsyncMock()
                scorer_mock.score = AsyncMock(return_value=scores)
                composer._scorer = scorer_mock

                result = await composer.compose(
                    lead={"id": 2, "business_name": "Budget Corp", "lead_score": 30},
                    research={"research_summary": "Budget test"},
                )

            # Only 1 iteration completed before budget cap
            assert result["iterations"] == 1
            assert result["subject"] == "Test"

    @pytest.mark.asyncio
    async def test_budget_exceeded_before_start(self):
        """If monthly budget is already exceeded, compose returns empty with flag."""
        with patch("titan.pipeline.rlm_composer._check_budget", new_callable=AsyncMock) as mock_budget:
            mock_budget.return_value = (False, "monthly cap reached ($105 >= $100)")

            from titan.pipeline.rlm_composer import RLMComposer
            composer = RLMComposer.__new__(RLMComposer)
            composer._scorer = AsyncMock()

            result = await composer.compose(
                lead={"id": 3, "business_name": "Broke Corp"},
                research={},
            )

            assert result["budget_exceeded"] is True
            assert result["iterations"] == 0
            assert result["subject"] == ""

    @pytest.mark.asyncio
    async def test_good_enough_stops_early(self):
        """If first draft scores good enough, compose stops after 1 iteration."""
        with patch("titan.pipeline.rlm_composer._check_budget", new_callable=AsyncMock) as mock_budget, \
             patch("titan.pipeline.rlm_composer._retrieve_context", new_callable=AsyncMock) as mock_ctx, \
             patch("titan.pipeline.rlm_composer._generate_draft", new_callable=AsyncMock) as mock_draft:

            mock_budget.return_value = (True, "ok")
            mock_ctx.return_value = ["context"]
            mock_draft.return_value = ('{"subject": "Perfect", "body": "Perfect email content here."}', 0.005)

            scores = {"clarity": 0.95, "specificity": 0.9, "authenticity": 0.95, "value_density": 0.9, "slop_score": 0.02}

            with patch("titan.pipeline.rlm_composer._is_good_enough", return_value=True):
                from titan.pipeline.rlm_composer import RLMComposer
                composer = RLMComposer.__new__(RLMComposer)
                scorer_mock = AsyncMock()
                scorer_mock.score = AsyncMock(return_value=scores)
                composer._scorer = scorer_mock

                result = await composer.compose(
                    lead={"id": 4, "business_name": "Lucky Corp", "lead_score": 90},
                    research={"research_summary": "Great lead"},
                )

            assert result["iterations"] == 1
            assert result["subject"] == "Perfect"


class TestContextExpansion:
    """Context expansion targeting weak dimensions."""

    @pytest.mark.asyncio
    async def test_expand_context_on_low_specificity(self):
        """When specificity is lowest, expansion queries for competitor data."""
        with patch("titan.pipeline.rlm_composer._qdrant_client") as mock_qdrant:
            mock_result = MagicMock()
            mock_result.metadata = {"text": "Competitor data for plumbing"}
            mock_qdrant.query.return_value = [mock_result]

            from titan.pipeline.rlm_composer import _expand_context
            items = await _expand_context(
                lead={"industry": "plumbing", "business_name": "Pipes Inc"},
                research={},
                weak_dimension="specificity",
            )
            assert len(items) == 1
            assert "Competitor data" in items[0]

    @pytest.mark.asyncio
    async def test_expand_context_no_qdrant(self):
        """When Qdrant is unavailable, expansion returns empty list."""
        with patch("titan.pipeline.rlm_composer._qdrant_client", None):
            from titan.pipeline.rlm_composer import _expand_context
            items = await _expand_context(
                lead={"industry": "test"},
                research={},
                weak_dimension="specificity",
            )
            assert items == []

    def test_find_weakest_dimension(self):
        """Should find the lowest-scoring dimension, excluding slop_score."""
        from titan.pipeline.rlm_composer import _find_weakest_dimension
        scores = {
            "clarity": 0.8,
            "specificity": 0.3,
            "authenticity": 0.7,
            "value_density": 0.6,
            "slop_score": 0.1,  # Should be excluded
        }
        assert _find_weakest_dimension(scores) == "specificity"


class TestQdrantRetrieval:
    """Qdrant vector retrieval for context."""

    @pytest.mark.asyncio
    async def test_retrieval_returns_context_items(self):
        """Should query Qdrant and return text from results."""
        with patch("titan.pipeline.rlm_composer._qdrant_client") as mock_qdrant:
            result1 = MagicMock()
            result1.metadata = {"text": "Review: Great plumber, fast service"}
            result2 = MagicMock()
            result2.metadata = {"content": "Pricing: $150 for basic repair"}
            mock_qdrant.query.return_value = [result1, result2]

            from titan.pipeline.rlm_composer import _retrieve_context
            items = await _retrieve_context(
                lead={"id": 10, "business_name": "Quick Plumb", "industry": "plumbing"},
                research={"pain_points": "no website", "research_summary": "local plumber"},
            )
            assert len(items) == 2
            assert "Great plumber" in items[0]
            assert "Pricing" in items[1]

    @pytest.mark.asyncio
    async def test_retrieval_no_qdrant(self):
        """When Qdrant is unavailable, retrieval returns empty list."""
        with patch("titan.pipeline.rlm_composer._qdrant_client", None):
            from titan.pipeline.rlm_composer import _retrieve_context
            items = await _retrieve_context(
                lead={"id": 11, "business_name": "Test"},
                research={},
            )
            assert items == []


class TestMem0Storage:
    """Mem0 write path for research context."""

    @pytest.mark.asyncio
    async def test_store_research_context(self):
        """Should store research in Mem0 and return context_id."""
        mock_mem0 = MagicMock()
        with patch("titan.pipeline.rlm_composer._mem0_client", mock_mem0), \
             patch("shared.db.execute", new_callable=AsyncMock) as mock_exec:

            from titan.pipeline.rlm_composer import store_research_context
            ctx_id = await store_research_context(
                lead_id=100,
                research={"competitors": "Acme Plumbing", "reviews": "4.5 stars"},
            )

            assert ctx_id is not None
            assert ctx_id.startswith("lead-100-")
            mock_mem0.add.assert_called_once()
            mock_exec.assert_called_once()  # DB update

    @pytest.mark.asyncio
    async def test_store_no_mem0(self):
        """When Mem0 is unavailable, returns None gracefully."""
        with patch("titan.pipeline.rlm_composer._mem0_client", None):
            from titan.pipeline.rlm_composer import store_research_context
            ctx_id = await store_research_context(lead_id=101, research={"test": "data"})
            assert ctx_id is None


# ---------------------------------------------------------------------------
# Email pipeline integration tests (shadow mode + feature flags)
# ---------------------------------------------------------------------------


class TestFeatureFlagOff:
    """When RLM is disabled, original compose path should be used."""

    @pytest.mark.asyncio
    async def test_rlm_disabled_env(self):
        """is_rlm_enabled returns False when env var is unset."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_RLM", None)
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=None):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is False

    @pytest.mark.asyncio
    async def test_rlm_disabled_explicit(self):
        """is_rlm_enabled returns False when explicitly set to false."""
        with patch.dict(os.environ, {"ENABLE_RLM": "false"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=False):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is False


class TestFeatureFlagOn:
    """When RLM is enabled, the RLM compose path should be used."""

    @pytest.mark.asyncio
    async def test_rlm_enabled_via_env(self):
        """is_rlm_enabled returns True when env var is set."""
        with patch.dict(os.environ, {"ENABLE_RLM": "true"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=None):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is True

    @pytest.mark.asyncio
    async def test_rlm_enabled_via_system_config(self):
        """system_config takes precedence over env var."""
        with patch.dict(os.environ, {"ENABLE_RLM": "false"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=True):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is True

    @pytest.mark.asyncio
    async def test_rlm_disabled_via_system_config_overrides_env(self):
        """system_config False overrides env var True (instant rollback)."""
        with patch.dict(os.environ, {"ENABLE_RLM": "true"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=False):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is False


class TestShadowMode:
    """Shadow mode: run both paths, log comparison, return original."""

    @pytest.mark.asyncio
    async def test_shadow_mode_enabled(self):
        """Shadow mode returns True when system_config says so."""
        with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=True):
            from titan.pipeline.email_compose import is_rlm_shadow_mode
            assert await is_rlm_shadow_mode() is True

    @pytest.mark.asyncio
    async def test_shadow_mode_env_fallback(self):
        """Shadow mode falls back to RLM_SHADOW_MODE env var."""
        with patch.dict(os.environ, {"RLM_SHADOW_MODE": "true"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, return_value=None):
                from titan.pipeline.email_compose import is_rlm_shadow_mode
                assert await is_rlm_shadow_mode() is True

    @pytest.mark.asyncio
    async def test_shadow_compose_runs_both_paths(self):
        """In shadow mode, both original and RLM paths execute."""
        from titan.pipeline.email_compose import _compose_with_rlm

        lead = {
            "id": 50, "business_name": "Shadow Corp", "industry": "tech",
            "contact_name": "John", "city": "NYC", "country": "US",
            "research_summary": "Great company", "lead_score": 70,
            "language": "en",
        }

        with patch("titan.pipeline.email_compose.is_rlm_shadow_mode", new_callable=AsyncMock, return_value=True), \
             patch("titan.pipeline.email_compose._original_compose_draft", new_callable=AsyncMock) as mock_orig, \
             patch("titan.pipeline.email_compose._get_rlm_composer") as mock_get_composer, \
             patch("titan.pipeline.email_compose._log_ab_comparison", new_callable=AsyncMock) as mock_log, \
             patch("titan.pipeline.email_compose.validate_email_content", return_value=(True, [])), \
             patch("titan.pipeline.email_compose._anti_slop_gate", new_callable=AsyncMock, return_value=("Orig Sub", "Original body text.", True)), \
             patch("titan.pipeline.email_compose.fetch_one", new_callable=AsyncMock, return_value={"id": 1}), \
             patch("titan.pipeline.email_compose.transition_lead", new_callable=AsyncMock):

            mock_orig.return_value = ("Orig Sub", "Original body text.")

            rlm_mock = AsyncMock()
            rlm_mock.compose = AsyncMock(return_value={
                "subject": "RLM Sub", "body": "RLM body.",
                "composite": 0.85, "scores": {"clarity": 0.9}, "iterations": 2, "budget_used": 0.02,
            })
            mock_get_composer.return_value = rlm_mock

            result = await _compose_with_rlm(lead, "soul", "tips", "rules", "hash123")

            assert result is True
            mock_orig.assert_called_once()  # Original path ran
            rlm_mock.compose.assert_called_once()  # RLM path ran
            mock_log.assert_called_once()  # Comparison logged

    @pytest.mark.asyncio
    async def test_shadow_mode_returns_original(self):
        """Shadow mode stores the original email, not RLM."""
        from titan.pipeline.email_compose import _compose_with_rlm

        lead = {
            "id": 51, "business_name": "Safe Corp", "industry": "retail",
            "contact_name": "Jane", "city": "LA", "country": "US",
            "research_summary": "Retail business", "lead_score": 60,
            "language": "en",
        }

        stored_args = {}

        async def capture_fetch_one(query, params):
            stored_args["subject"] = params[1]
            stored_args["body"] = params[2]
            return {"id": 1}

        with patch("titan.pipeline.email_compose.is_rlm_shadow_mode", new_callable=AsyncMock, return_value=True), \
             patch("titan.pipeline.email_compose._original_compose_draft", new_callable=AsyncMock, return_value=("Original Subject", "Original body here.")), \
             patch("titan.pipeline.email_compose._get_rlm_composer") as mock_comp, \
             patch("titan.pipeline.email_compose._log_ab_comparison", new_callable=AsyncMock), \
             patch("titan.pipeline.email_compose.validate_email_content", return_value=(True, [])), \
             patch("titan.pipeline.email_compose._anti_slop_gate", new_callable=AsyncMock, return_value=("Original Subject", "Original body here.", True)), \
             patch("titan.pipeline.email_compose.fetch_one", new_callable=AsyncMock, side_effect=capture_fetch_one), \
             patch("titan.pipeline.email_compose.transition_lead", new_callable=AsyncMock):

            rlm_mock = AsyncMock()
            rlm_mock.compose = AsyncMock(return_value={
                "subject": "RLM Better", "body": "RLM produced this.",
                "composite": 0.95, "scores": {}, "iterations": 3, "budget_used": 0.03,
            })
            mock_comp.return_value = rlm_mock

            await _compose_with_rlm(lead, "soul", "tips", "rules", "hash456")

            # Should store the original, NOT the RLM version
            assert stored_args["subject"] == "Original Subject"
            assert stored_args["body"] == "Original body here."


class TestFullRLMMode:
    """Feature flag on + shadow off = full RLM compose."""

    @pytest.mark.asyncio
    async def test_full_rlm_compose(self):
        """Full RLM mode stores the RLM-generated email."""
        from titan.pipeline.email_compose import _compose_with_rlm

        lead = {
            "id": 60, "business_name": "Full RLM Corp", "industry": "dental",
            "contact_name": "Dr. Smith", "city": "Chicago", "country": "US",
            "research_summary": "Dental practice", "lead_score": 85,
            "language": "en",
        }

        stored_args = {}

        async def capture_fetch_one(query, params):
            stored_args["subject"] = params[1]
            stored_args["body"] = params[2]
            return {"id": 1}

        with patch("titan.pipeline.email_compose.is_rlm_shadow_mode", new_callable=AsyncMock, return_value=False), \
             patch("titan.pipeline.email_compose._get_rlm_composer") as mock_comp, \
             patch("titan.pipeline.email_compose.validate_email_content", return_value=(True, [])), \
             patch("titan.pipeline.email_compose._anti_slop_gate", new_callable=AsyncMock, return_value=("RLM Subject", "RLM composed body text.", True)), \
             patch("titan.pipeline.email_compose.fetch_one", new_callable=AsyncMock, side_effect=capture_fetch_one), \
             patch("titan.pipeline.email_compose.transition_lead", new_callable=AsyncMock):

            rlm_mock = AsyncMock()
            rlm_mock.compose = AsyncMock(return_value={
                "subject": "RLM Subject", "body": "RLM composed body text.",
                "composite": 0.88, "scores": {"clarity": 0.9}, "iterations": 2, "budget_used": 0.025,
            })
            mock_comp.return_value = rlm_mock

            result = await _compose_with_rlm(lead, "soul", "tips", "rules", "hash789")

            assert result is True
            assert stored_args["subject"] == "RLM Subject"
            assert stored_args["body"] == "RLM composed body text."

    @pytest.mark.asyncio
    async def test_rlm_budget_exceeded_returns_false(self):
        """When RLM budget is exceeded, returns False so caller falls back."""
        from titan.pipeline.email_compose import _compose_with_rlm

        lead = {
            "id": 61, "business_name": "Broke Corp", "industry": "food",
            "research_summary": "Restaurant", "lead_score": 40,
        }

        with patch("titan.pipeline.email_compose.is_rlm_shadow_mode", new_callable=AsyncMock, return_value=False), \
             patch("titan.pipeline.email_compose._get_rlm_composer") as mock_comp:

            rlm_mock = AsyncMock()
            rlm_mock.compose = AsyncMock(return_value={
                "subject": "", "body": "", "composite": 0.0,
                "iterations": 0, "budget_used": 0.0,
                "budget_exceeded": True, "reason": "monthly cap reached",
            })
            mock_comp.return_value = rlm_mock

            result = await _compose_with_rlm(lead, "soul", "tips")
            assert result is False


class TestABComparison:
    """A/B comparison logging."""

    @pytest.mark.asyncio
    async def test_log_comparison_records_event(self):
        """_log_ab_comparison should emit an event with scores from both paths."""
        from titan.pipeline.email_compose import _log_ab_comparison

        with patch("titan.pipeline.email_compose._slop_scorer") as mock_scorer, \
             patch("shared.db.emit_event", new_callable=AsyncMock) as mock_emit:

            mock_scorer.score = AsyncMock(return_value={
                "clarity": 0.6, "specificity": 0.5, "authenticity": 0.6,
                "value_density": 0.5, "slop_score": 0.2,
            })

            rlm_result = {
                "composite": 0.85,
                "scores": {"clarity": 0.9, "specificity": 0.85},
                "iterations": 2,
                "budget_used": 0.02,
            }

            await _log_ab_comparison(42, "Test Subject", "Test body text.", rlm_result)

            mock_emit.assert_called_once()
            event_data = mock_emit.call_args[0][1]
            assert event_data["lead_id"] == "42"
            assert event_data["rlm_won"] is True
            assert event_data["rlm"]["iterations"] == 2


class TestRuntimeToggle:
    """Runtime toggle via set_rlm_enabled / set_rlm_shadow_mode."""

    @pytest.mark.asyncio
    async def test_set_rlm_enabled(self):
        """set_rlm_enabled writes to system_config."""
        with patch("shared.db.set_config", new_callable=AsyncMock) as mock_set:
            from titan.pipeline.email_compose import set_rlm_enabled
            await set_rlm_enabled(True)
            mock_set.assert_called_once_with("rlm_enabled", True)

    @pytest.mark.asyncio
    async def test_set_rlm_shadow_mode(self):
        """set_rlm_shadow_mode writes to system_config."""
        with patch("shared.db.set_config", new_callable=AsyncMock) as mock_set:
            from titan.pipeline.email_compose import set_rlm_shadow_mode
            await set_rlm_shadow_mode(False)
            mock_set.assert_called_once_with("rlm_shadow_mode", False)

    @pytest.mark.asyncio
    async def test_db_failure_falls_back_to_env(self):
        """If system_config DB is unavailable, falls back to env var."""
        with patch.dict(os.environ, {"ENABLE_RLM": "true"}):
            with patch("titan.pipeline.email_compose.get_config", new_callable=AsyncMock, side_effect=Exception("DB down")):
                from titan.pipeline.email_compose import is_rlm_enabled
                assert await is_rlm_enabled() is True
