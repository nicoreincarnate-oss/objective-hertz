"""Phase 43: Tier classifier tests."""

from __future__ import annotations

import pytest


class TestRuleBasedClassifier:
    @pytest.mark.asyncio
    async def test_architecture_keyword_routes_to_genius(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "Design the architecture for a multi-tenant SaaS",
            operation="",
            daemon="openjarvis",
        )
        assert result.tier == TierName.GENIUS
        assert result.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_long_prompt_routes_to_longctx(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        long_prompt = "x" * 500_000
        result = await clf.classify(long_prompt, operation="", daemon="deerflow")
        assert result.tier == TierName.LONGCTX

    @pytest.mark.asyncio
    async def test_simple_classification_routes_to_fast(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "Classify this short text",
            operation="simple_classification",
            daemon="hermes",
            max_output_tokens=200,
        )
        assert result.tier == TierName.FAST

    @pytest.mark.asyncio
    async def test_aider_editor_routes_to_local(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "Apply this patch",
            operation="aider_editor",
            daemon="ruflo",
        )
        assert result.tier == TierName.LOCAL

    @pytest.mark.asyncio
    async def test_aider_architect_routes_to_smart(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "Plan a fix for the failing test",
            operation="aider_architect",
            daemon="ruflo",
        )
        assert result.tier == TierName.SMART

    @pytest.mark.asyncio
    async def test_vision_operation_routes_to_vision(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "Score this screenshot",
            operation="visual_qa",
            daemon="clawdbot",
        )
        assert result.tier == TierName.VISION

    @pytest.mark.asyncio
    async def test_default_falls_back_to_smart(self):
        from shared.tier_classifier import RuleBasedClassifier
        from shared.tiers import TierName
        clf = RuleBasedClassifier()
        result = await clf.classify(
            "An ambiguous task with no signals",
            operation="unknown_op",
            daemon="unknown_daemon",
        )
        assert result.tier == TierName.SMART
        assert "default" in result.reasoning.lower()


class TestClassifierFactory:
    def test_default_returns_rules(self, monkeypatch):
        monkeypatch.delenv("CLASSIFIER_MODEL", raising=False)
        from shared.tier_classifier import get_classifier, RuleBasedClassifier
        clf = get_classifier()
        assert isinstance(clf, RuleBasedClassifier)

    def test_qwen_env_returns_ml(self, monkeypatch):
        monkeypatch.setenv("CLASSIFIER_MODEL", "qwen-0.5b")
        from shared.tier_classifier import get_classifier, LocalMLClassifier
        clf = get_classifier()
        assert isinstance(clf, LocalMLClassifier)
