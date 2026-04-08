"""Phase 41: Tier definitions + budget guard tests."""

from __future__ import annotations

import pytest


class TestTierDefinitionsComplete:
    def test_each_tier_has_required_fields(self):
        from shared.tiers import TIERS
        for tier_name, config in TIERS.items():
            assert config.primary_model, f"{tier_name}: missing primary_model"
            assert config.context_window > 0, f"{tier_name}: missing context_window"
            assert config.max_output_tokens > 0, f"{tier_name}: missing max_output_tokens"
            assert isinstance(config.fallback_chain, list), f"{tier_name}: bad fallback_chain"
            assert config.use_when, f"{tier_name}: empty use_when"

    def test_no_duplicate_primary_models_in_premium(self):
        """Different premium tiers should map to distinct provider models."""
        from shared.tiers import TIERS, TierName
        premium = [TierName.GENIUS, TierName.SMART, TierName.CODEX, TierName.AGENTIC]
        models = [TIERS[t].primary_model for t in premium]
        assert len(models) == len(set(models)), f"Duplicates: {models}"


class TestFallbackChains:
    def test_fallback_chains_terminate(self):
        """Every fallback chain must eventually reach a terminal tier (local/local-heavy)."""
        from shared.tiers import TIERS, TierName
        terminal = {TierName.LOCAL, TierName.LOCAL_HEAVY}
        for tier_name, config in TIERS.items():
            if tier_name in terminal:
                continue
            chain = config.fallback_chain
            assert any(t in terminal for t in chain) or any(
                tt in terminal
                for ft in chain
                for tt in TIERS.get(ft, config).fallback_chain
            ), f"{tier_name}: fallback chain doesn't reach terminal: {chain}"

    def test_no_fallback_to_self(self):
        from shared.tiers import TIERS
        for tier_name, config in TIERS.items():
            assert tier_name not in config.fallback_chain, f"{tier_name}: self-referential fallback"


class TestPerDaemonBudget:
    def test_budget_caps_defined_for_all_daemons(self):
        """Every daemon should have a budget cap defined."""
        expected_daemons = {
            "titan", "clawdbot", "deerflow", "ruflo",
            "hermes", "perseus", "openjarvis", "conway",
        }
        # In a real test we'd check the SQL migration or fetch from DB.
        # Here we just sanity-check the migration file lists all daemons.
        from pathlib import Path
        migration_path = Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "046-tier-spend-tracking.sql"
        if not migration_path.exists():
            pytest.skip("migration not yet copied")
        sql = migration_path.read_text()
        for daemon in expected_daemons:
            assert f"'{daemon}'" in sql, f"daemon {daemon} missing from migration"


class TestSpendAlerts:
    def test_threshold_levels(self):
        from shared.spend_alerts import THRESHOLDS, AlertLevel
        levels = {t[1] for t in THRESHOLDS}
        assert AlertLevel.INFO in levels
        assert AlertLevel.WARNING in levels
        assert AlertLevel.CRITICAL in levels
        assert AlertLevel.BLOCK in levels

    def test_thresholds_ordered_high_to_low(self):
        from shared.spend_alerts import THRESHOLDS
        pcts = [t[0] for t in THRESHOLDS]
        assert pcts == sorted(pcts, reverse=True), "Thresholds must be ordered high → low"

    def test_block_level_includes_block_action(self):
        from shared.spend_alerts import _actions_for_level, AlertLevel
        actions = _actions_for_level(AlertLevel.BLOCK)
        assert "block_new_requests" in actions
