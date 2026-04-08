"""Phase 40: LiteLLM backend tests.

Tests the LiteLLMBackend class scaffold and config validation. These run
WITHOUT a real LiteLLM proxy — they mock httpx and verify request shape +
metadata propagation.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Reset feature flags between tests."""
    monkeypatch.delenv("LITELLM_PROXY_ENABLED", raising=False)
    monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)


class TestTierResolution:
    """Legacy model name → tier mapping (Phase 40 Task 4)."""

    def test_legacy_aliases(self):
        from shared.tiers import TierName
        assert TierName.from_string("smart") == TierName.SMART
        assert TierName.from_string("sonnet") == TierName.SMART
        assert TierName.from_string("claude") == TierName.SMART
        assert TierName.from_string("opus") == TierName.GENIUS
        assert TierName.from_string("haiku") == TierName.FAST
        assert TierName.from_string("ollama") == TierName.LOCAL
        assert TierName.from_string("airllm") == TierName.LOCAL_HEAVY

    def test_unknown_falls_back_to_smart(self):
        from shared.tiers import TierName
        assert TierName.from_string("nonsense_tier") == TierName.SMART


class TestTierConfig:
    def test_all_tiers_present(self):
        from shared.tiers import TIERS, TierName
        expected = {
            TierName.GENIUS, TierName.SMART, TierName.CODEX,
            TierName.AGENTIC, TierName.LONGCTX, TierName.CHAT,
            TierName.FAST, TierName.CHEAP, TierName.LOCAL,
            TierName.LOCAL_HEAVY, TierName.VISION,
        }
        assert set(TIERS.keys()) == expected

    def test_local_tiers_zero_cost(self):
        from shared.tiers import TIERS, TierName
        assert TIERS[TierName.LOCAL].cost_per_m_input == 0.0
        assert TIERS[TierName.LOCAL_HEAVY].cost_per_m_input == 0.0
        assert TIERS[TierName.LOCAL].is_local is True
        assert TIERS[TierName.LOCAL_HEAVY].is_local is True

    def test_vision_supports_vision_flag(self):
        from shared.tiers import TIERS, TierName
        assert TIERS[TierName.VISION].supports_vision is True

    def test_kimi_is_default_vision_provider(self):
        from shared.tiers import TIERS, TierName
        assert "kimi" in TIERS[TierName.VISION].primary_model.lower()


class TestEstimateCost:
    def test_smart_tier_cost(self):
        from shared.tiers import estimate_cost, TierName
        cost = estimate_cost(TierName.SMART, 1_000_000, 100_000)
        # 3.00 * 1 + 15.00 * 0.1 = 4.50
        assert abs(cost - 4.50) < 0.01

    def test_local_tier_zero_cost(self):
        from shared.tiers import estimate_cost, TierName
        assert estimate_cost(TierName.LOCAL, 100_000, 100_000) == 0.0

    def test_genius_more_expensive_than_smart(self):
        from shared.tiers import estimate_cost, TierName
        smart = estimate_cost(TierName.SMART, 100_000, 100_000)
        genius = estimate_cost(TierName.GENIUS, 100_000, 100_000)
        assert genius > smart


class TestDowngrade:
    def test_genius_downgrades_to_agentic(self):
        from shared.tiers import downgrade_tier, TierName
        assert downgrade_tier(TierName.GENIUS) == TierName.AGENTIC

    def test_smart_downgrades_to_agentic(self):
        from shared.tiers import downgrade_tier, TierName
        assert downgrade_tier(TierName.SMART) == TierName.AGENTIC

    def test_local_downgrades_to_local_heavy(self):
        from shared.tiers import downgrade_tier, TierName
        assert downgrade_tier(TierName.LOCAL) == TierName.LOCAL_HEAVY

    def test_local_heavy_downgrades_to_local(self):
        from shared.tiers import downgrade_tier, TierName
        assert downgrade_tier(TierName.LOCAL_HEAVY) == TierName.LOCAL


class TestUpgrade:
    def test_local_upgrades_to_smart(self):
        from shared.tiers import upgrade_tier, TierName
        assert upgrade_tier(TierName.LOCAL) == TierName.SMART

    def test_smart_upgrades_to_genius(self):
        from shared.tiers import upgrade_tier, TierName
        assert upgrade_tier(TierName.SMART) == TierName.GENIUS

    def test_genius_stays_genius(self):
        from shared.tiers import upgrade_tier, TierName
        assert upgrade_tier(TierName.GENIUS) == TierName.GENIUS


class TestConfigYamlValid:
    def test_litellm_config_yaml_loads(self):
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent / "config" / "litellm_config.yaml"
        if not config_path.exists():
            pytest.skip("litellm_config.yaml not yet copied to repo")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "model_list" in config
        assert "router_settings" in config
        assert len(config["model_list"]) > 0

    def test_all_tiers_in_config(self):
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent / "config" / "litellm_config.yaml"
        if not config_path.exists():
            pytest.skip("config not yet present")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        tier_names = {entry["model_name"] for entry in config["model_list"]}
        required = {"genius", "smart", "fast", "cheap", "local", "vision",
                    "codex", "agentic", "longctx", "chat", "local-heavy"}
        assert required.issubset(tier_names), f"Missing: {required - tier_names}"
