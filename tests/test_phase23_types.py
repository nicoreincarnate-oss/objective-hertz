"""Tests for Phase 23 Task 23-01: Unified LLM protocol and types.

Validates:
- ModelTier enum values and aliases
- FallbackStep and FallbackChain dataclasses
- LLMResponse dataclass with defaults
- LLMProvider protocol structure
- DEFAULT_CHAINS coverage
- resolve_tier() with valid and invalid inputs
"""

from __future__ import annotations

from shared.llm_unified import (
    DEFAULT_CHAINS,
    FallbackChain,
    FallbackStep,
    LLMProvider,
    LLMResponse,
    ModelTier,
    resolve_tier,
)


class TestModelTier:
    """ModelTier enum has expected values."""

    def test_tier_values(self):
        assert ModelTier.GENIUS.value == "genius"
        assert ModelTier.SMART.value == "smart"
        assert ModelTier.FAST.value == "fast"
        assert ModelTier.LOCAL.value == "local"
        assert ModelTier.LOCAL_SMALL.value == "local-small"
        assert ModelTier.LOCAL_HEAVY.value == "local-heavy"
        assert ModelTier.EMBED.value == "embed"

    def test_tier_is_string(self):
        assert isinstance(ModelTier.GENIUS, str)
        assert ModelTier.GENIUS == "genius"

    def test_all_tiers_counted(self):
        # PORT-PLAN Phase 2 Wave 2 (02-02): ModelTier merged with TierName.
        # 13 tiers total: 11 generative (genius/smart/codex/agentic/longctx/chat/
        # fast/cheap/local/local-heavy/vision) + LOCAL_SMALL + EMBED.
        assert len(ModelTier) == 13


class TestResolveTier:
    """resolve_tier() handles aliases and unknown values."""

    def test_direct_tier(self):
        assert resolve_tier("genius") == ModelTier.GENIUS
        assert resolve_tier("smart") == ModelTier.SMART
        assert resolve_tier("fast") == ModelTier.FAST

    def test_aliases(self):
        # PORT-PLAN decision #1 (02-02): auto → SMART, not FAST. Quality > cost.
        assert resolve_tier("auto") == ModelTier.SMART
        assert resolve_tier("primary") == ModelTier.SMART
        assert resolve_tier("airllm") == ModelTier.LOCAL_HEAVY
        assert resolve_tier("research-local") == ModelTier.LOCAL_HEAVY

    def test_unknown_defaults_to_smart(self):
        # PORT-PLAN decision #1 (02-02): unknown tiers default to SMART (Sonnet),
        # not FAST (Haiku). Quality > cost on the fallback default.
        assert resolve_tier("nonexistent") == ModelTier.SMART


class TestFallbackStep:
    """FallbackStep is frozen dataclass with defaults."""

    def test_basic_creation(self):
        step = FallbackStep(
            tier=ModelTier.GENIUS,
            model_id="claude-opus-4-6",
            engine="anthropic",
        )
        assert step.tier == ModelTier.GENIUS
        assert step.model_id == "claude-opus-4-6"
        assert step.engine == "anthropic"
        assert step.timeout_seconds == 120.0

    def test_custom_timeout(self):
        step = FallbackStep(
            tier=ModelTier.FAST,
            model_id="claude-haiku-4-5",
            engine="anthropic",
            timeout_seconds=30.0,
        )
        assert step.timeout_seconds == 30.0

    def test_frozen(self):
        step = FallbackStep(tier=ModelTier.FAST, model_id="test", engine="anthropic")
        try:
            step.tier = ModelTier.GENIUS
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass


class TestFallbackChain:
    """FallbackChain is iterable and has len()."""

    def test_empty_chain(self):
        chain = FallbackChain()
        assert len(chain) == 0
        assert list(chain) == []

    def test_chain_with_steps(self):
        steps = [
            FallbackStep(ModelTier.GENIUS, "opus", "anthropic"),
            FallbackStep(ModelTier.SMART, "sonnet", "anthropic"),
        ]
        chain = FallbackChain(steps)
        assert len(chain) == 2
        assert list(chain) == steps

    def test_chain_iteration(self):
        steps = [
            FallbackStep(ModelTier.FAST, "haiku", "anthropic"),
            FallbackStep(ModelTier.LOCAL, "", "ollama"),
        ]
        chain = FallbackChain(steps)
        for i, step in enumerate(chain):
            assert step == steps[i]


class TestDefaultChains:
    """DEFAULT_CHAINS has entries for key tiers."""

    def test_genius_chain_exists(self):
        assert ModelTier.GENIUS in DEFAULT_CHAINS
        chain = DEFAULT_CHAINS[ModelTier.GENIUS]
        assert len(chain) == 4
        assert chain.steps[0].tier == ModelTier.GENIUS
        assert chain.steps[-1].engine == "ollama"

    def test_smart_chain_exists(self):
        assert ModelTier.SMART in DEFAULT_CHAINS
        chain = DEFAULT_CHAINS[ModelTier.SMART]
        assert len(chain) == 3
        assert chain.steps[0].tier == ModelTier.SMART

    def test_fast_chain_exists(self):
        assert ModelTier.FAST in DEFAULT_CHAINS
        chain = DEFAULT_CHAINS[ModelTier.FAST]
        assert len(chain) == 2
        assert chain.steps[0].tier == ModelTier.FAST
        assert chain.steps[1].engine == "ollama"

    def test_local_chain_exists(self):
        assert ModelTier.LOCAL in DEFAULT_CHAINS
        chain = DEFAULT_CHAINS[ModelTier.LOCAL]
        assert len(chain) == 2

    def test_all_chains_end_with_local(self):
        for tier, chain in DEFAULT_CHAINS.items():
            if tier != ModelTier.LOCAL:
                assert chain.steps[-1].engine == "ollama", (
                    f"Chain for {tier.value} should end with ollama"
                )


class TestLLMResponse:
    """LLMResponse dataclass has expected defaults."""

    def test_minimal_creation(self):
        resp = LLMResponse(
            content="hello",
            model_id="claude-sonnet-4-6",
            engine="anthropic",
            tier_requested=ModelTier.SMART,
            tier_actual=ModelTier.SMART,
        )
        assert resp.content == "hello"
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.cost_usd == 0.0
        assert resp.latency_ms == 0
        assert resp.fallback_triggered is False
        assert resp.fallback_depth == 0
        assert resp.raw_usage == {}

    def test_fallback_metadata(self):
        resp = LLMResponse(
            content="recovered",
            model_id="claude-haiku-4-5",
            engine="anthropic",
            tier_requested=ModelTier.GENIUS,
            tier_actual=ModelTier.FAST,
            fallback_triggered=True,
            fallback_reason="Opus overloaded",
            fallback_depth=2,
        )
        assert resp.fallback_triggered is True
        assert resp.fallback_depth == 2
        assert "Opus" in resp.fallback_reason

    def test_cost_and_tokens(self):
        resp = LLMResponse(
            content="result",
            model_id="claude-sonnet-4-6",
            engine="anthropic",
            tier_requested=ModelTier.SMART,
            tier_actual=ModelTier.SMART,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.0135,
        )
        assert resp.input_tokens == 1000
        assert resp.output_tokens == 500
        assert resp.cost_usd == 0.0135
