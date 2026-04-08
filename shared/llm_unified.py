"""Unified LLM provider -- single factory for all inference paths.

Phase 23: Defines the core protocol, configuration types, and fallback chain
structures used by the UnifiedLLMFactory and all providers.

When ANATOMY_UNIFIED_LLM=true, these types replace the ad-hoc model/tier
strings used throughout the codebase with structured, type-safe equivalents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("perseus.llm.unified")


# PORT-PLAN Phase 2 Wave 2 (02-02): TierName and ModelTier are now ONE class.
# `shared.tiers.TierName` is the canonical 13-tier enum (11 generative + LOCAL_SMALL + EMBED).
# `ModelTier` here is a re-export so legacy `from shared.llm_unified import ModelTier`
# call sites keep working unchanged. They are literally `is` the same class.
from shared.tiers import TierName as ModelTier  # noqa: E402

# Decision #1 from PORT-PLAN §9: `auto` resolves to SMART (Sonnet 4.6),
# NOT FAST (Haiku). This is the operator-locked default — quality > cost on
# the routing default. Per-call explicit tier still wins.
_TIER_ALIASES: dict[str, ModelTier] = {
    "auto": ModelTier.SMART,
    "primary": ModelTier.SMART,
    "default": ModelTier.SMART,
    "claude": ModelTier.SMART,
    "claude-sonnet": ModelTier.SMART,
    "sonnet": ModelTier.SMART,
    "opus": ModelTier.GENIUS,
    "claude-opus": ModelTier.GENIUS,
    "haiku": ModelTier.FAST,
    "ollama": ModelTier.LOCAL,
    "airllm": ModelTier.LOCAL_HEAVY,
    "research-local": ModelTier.LOCAL_HEAVY,
    "ollm": ModelTier.LOCAL_HEAVY,
    "huge-context-local": ModelTier.LOCAL_HEAVY,
}


def resolve_tier(model: str) -> ModelTier:
    """Resolve a model string to a ModelTier, handling aliases.

    Unknown tiers default to SMART (Sonnet) per decision #1 — quality-first.
    """
    if model in _TIER_ALIASES:
        return _TIER_ALIASES[model]
    try:
        return ModelTier(model)
    except ValueError:
        logger.debug("Unknown model tier %r, defaulting to SMART", model)
        return ModelTier.SMART


@dataclass(frozen=True)
class FallbackStep:
    """One step in the fallback chain."""

    tier: ModelTier
    model_id: str
    engine: str  # "anthropic", "ollama", "openai", "google", etc.
    timeout_seconds: float = 120.0


@dataclass
class FallbackChain:
    """Ordered fallback chain. First step is preferred, last is terminal."""

    steps: list[FallbackStep] = field(default_factory=list)

    def __iter__(self):
        return iter(self.steps)

    def __len__(self):
        return len(self.steps)


# Default graduated fallback chains per tier
DEFAULT_CHAINS: dict[ModelTier, FallbackChain] = {
    ModelTier.GENIUS: FallbackChain(
        [
            FallbackStep(ModelTier.GENIUS, "claude-opus-4-6", "anthropic"),
            FallbackStep(ModelTier.SMART, "claude-sonnet-4-6", "anthropic"),
            FallbackStep(ModelTier.FAST, "claude-haiku-4-5", "anthropic"),
            FallbackStep(ModelTier.LOCAL, "", "ollama"),  # model_id resolved at runtime
        ]
    ),
    ModelTier.SMART: FallbackChain(
        [
            FallbackStep(ModelTier.SMART, "claude-sonnet-4-6", "anthropic"),
            FallbackStep(ModelTier.FAST, "claude-haiku-4-5", "anthropic"),
            FallbackStep(ModelTier.LOCAL, "", "ollama"),
        ]
    ),
    ModelTier.FAST: FallbackChain(
        [
            FallbackStep(ModelTier.FAST, "claude-haiku-4-5", "anthropic"),
            FallbackStep(ModelTier.LOCAL, "", "ollama"),
        ]
    ),
    ModelTier.LOCAL: FallbackChain(
        [
            FallbackStep(ModelTier.LOCAL, "", "ollama"),
            FallbackStep(ModelTier.LOCAL_SMALL, "", "ollama"),
        ]
    ),
}


@dataclass
class LLMResponse:
    """Structured response from any LLM call."""

    content: str
    model_id: str
    engine: str
    tier_requested: ModelTier
    tier_actual: ModelTier
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    fallback_triggered: bool = False
    fallback_reason: str = ""
    fallback_depth: int = 0  # 0 = primary, 1 = first fallback, etc.
    raw_usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """Protocol that all engine backends must satisfy."""

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model_id: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> LLMResponse: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...
