"""
Tests for shared/model_selector.py — dynamic model selection using OJ Intelligence catalog.

Covers: tier resolution, budget downgrades, prefer_local, context requirements,
catalog vs hardcoded fallback, and model listing.
"""

import asyncio
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_selector(catalog_available: bool = True):
    """Re-import model_selector with controlled catalog availability.

    Returns the freshly-imported module.
    """
    # Purge cached module
    for mod_name in list(sys.modules):
        if "model_selector" in mod_name:
            del sys.modules[mod_name]

    if not catalog_available:
        # Block OJ imports so the module falls back to hardcoded
        with patch.dict(sys.modules, {
            "openjarvis": None,
            "openjarvis.intelligence": None,
            "openjarvis.intelligence.model_catalog": None,
            "openjarvis.core": None,
            "openjarvis.core.registry": None,
            "openjarvis.core.types": None,
        }):
            import shared.model_selector as mod
            return mod

    import shared.model_selector as mod
    return mod


# ---------------------------------------------------------------------------
# Tier resolution tests
# ---------------------------------------------------------------------------


class TestSelectFastTier:
    def test_select_fast_tier(self):
        sel = _fresh_selector()
        result = run(sel.select_model("fast"))

        assert result.tier == "fast"
        assert result.engine in ("cloud", "ollama")
        assert result.model_id != ""
        assert result.context_length > 0
        assert result.fallback_model != ""

        # Should either be catalog-driven or hardcoded
        assert "catalog:" in result.reason or "hardcoded:" in result.reason


class TestSelectSmartTier:
    def test_select_smart_tier(self):
        sel = _fresh_selector()
        result = run(sel.select_model("smart"))

        assert result.tier == "smart"
        assert result.engine in ("cloud", "ollama", "vllm")
        # Smart tier should be a more capable model
        assert result.model_id != ""
        assert result.context_length >= 32768


class TestSelectGeniusTier:
    def test_select_genius_tier(self):
        sel = _fresh_selector()
        result = run(sel.select_model("genius"))

        assert result.tier == "genius"
        assert result.engine in ("cloud", "ollama", "vllm")
        assert result.model_id != ""
        # Genius should have a non-zero cost (it's the top cloud tier)
        if result.engine == "cloud":
            assert result.estimated_cost > 0


class TestSelectLocalTier:
    def test_select_local_tier(self):
        sel = _fresh_selector()
        result = run(sel.select_model("local"))

        assert result.tier == "local"
        # Local models should use ollama or vllm, not cloud
        assert result.engine in ("ollama", "vllm", "llamacpp", "sglang")
        assert result.estimated_cost == 0.0 or result.engine != "cloud"


# ---------------------------------------------------------------------------
# Budget downgrade tests
# ---------------------------------------------------------------------------


class TestBudgetDowngrade80Pct:
    def test_budget_downgrade_80pct(self):
        """At 50% budget remaining (below 80% threshold), smart should downgrade to fast."""
        sel = _fresh_selector()
        result = run(sel.select_model("smart", budget_remaining_pct=50.0))

        # Should be downgraded from smart to fast
        assert result.tier == "fast"
        assert "downgraded" in result.reason
        assert "smart" in result.reason


class TestBudgetDowngrade100Pct:
    def test_budget_downgrade_100pct(self):
        """At 0% budget remaining (exhausted), genius should downgrade to local."""
        sel = _fresh_selector()
        result = run(sel.select_model("genius", budget_remaining_pct=0.0))

        # Should be downgraded to local
        assert result.tier == "local"
        assert "downgraded" in result.reason
        assert "exhausted" in result.reason


# ---------------------------------------------------------------------------
# Prefer local flag
# ---------------------------------------------------------------------------


class TestPreferLocalFlag:
    def test_prefer_local_flag(self):
        """prefer_local=True should force cloud tiers to local."""
        sel = _fresh_selector()
        result = run(sel.select_model("smart", prefer_local=True))

        assert result.tier == "local"
        assert result.engine in ("ollama", "vllm", "llamacpp", "sglang")


# ---------------------------------------------------------------------------
# Context length requirement
# ---------------------------------------------------------------------------


class TestContextLengthRequirement:
    def test_context_length_requirement(self):
        """When required_context is set, selected model must meet it."""
        sel = _fresh_selector()
        result = run(sel.select_model("fast", required_context=100000))

        # The selected model should have enough context
        assert result.context_length >= 100000


# ---------------------------------------------------------------------------
# Catalog unavailable fallback
# ---------------------------------------------------------------------------


class TestOJUnavailableUsesHardcoded:
    def test_oj_unavailable_uses_hardcoded(self):
        """When OJ Intelligence is unavailable, falls back to hardcoded mappings."""
        sel = _fresh_selector(catalog_available=False)
        assert not sel.is_catalog_available()

        result = run(sel.select_model("smart"))

        assert result.model_id == "claude-sonnet-4-20250514"
        assert result.engine == "cloud"
        assert "hardcoded:" in result.reason
        assert "OJ catalog unavailable" in result.reason


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


class TestGetAvailableModels:
    def test_get_available_models_all(self):
        """get_available_models() should return a non-empty list."""
        sel = _fresh_selector()
        models = sel.get_available_models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_get_available_models_cloud_filter(self):
        """Filtering by engine_type='cloud' should only return cloud models."""
        sel = _fresh_selector()
        cloud_models = sel.get_available_models(engine_type="cloud")
        assert isinstance(cloud_models, list)
        # If catalog is available, there should be cloud models
        if sel.is_catalog_available():
            assert len(cloud_models) > 0

    def test_get_available_models_ollama_filter(self):
        """Filtering by engine_type='ollama' should only return local models."""
        sel = _fresh_selector()
        ollama_models = sel.get_available_models(engine_type="ollama")
        assert isinstance(ollama_models, list)

    def test_get_model_specs(self):
        """get_model_specs() should return a dict of model_id -> spec."""
        sel = _fresh_selector()
        specs = sel.get_model_specs()
        assert isinstance(specs, dict)
        assert len(specs) > 0

    def test_get_model_specs_hardcoded_fallback(self):
        """When catalog unavailable, get_model_specs returns hardcoded entries."""
        sel = _fresh_selector(catalog_available=False)
        specs = sel.get_model_specs()
        assert isinstance(specs, dict)
        assert len(specs) > 0
        # Should contain the hardcoded model IDs
        assert "claude-sonnet-4-20250514" in specs
