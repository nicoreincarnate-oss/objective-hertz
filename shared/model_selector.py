"""Dynamic model selection using OJ Intelligence catalog.

Replaces hardcoded tier->model mappings with catalog-driven selection.
Considers: task type, budget remaining, available engines, model capabilities.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("perseus.model_selector")

# ---------------------------------------------------------------------------
# Import OJ Intelligence catalog with guard
# ---------------------------------------------------------------------------

_OJ_CATALOG_AVAILABLE = False
_oj_builtin_models: list[Any] = []
_ModelRegistry: Any = None
_ModelSpec: Any = None

try:
    from openjarvis.intelligence.model_catalog import BUILTIN_MODELS, register_builtin_models
    from openjarvis.core.registry import ModelRegistry
    from openjarvis.core.types import ModelSpec

    _oj_builtin_models = BUILTIN_MODELS
    _ModelRegistry = ModelRegistry
    _ModelSpec = ModelSpec
    _OJ_CATALOG_AVAILABLE = True

    # Ensure builtin models are registered
    register_builtin_models()
    logger.debug("OJ Intelligence catalog loaded: %d models", len(BUILTIN_MODELS))
except (ImportError, AttributeError, TypeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
    logger.warning("OJ Intelligence catalog unavailable, using hardcoded mappings: %s", exc)
    _OJ_CATALOG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Hardcoded fallback mappings (mirrors llm_engine_adapter._OJ_TIER_MAP)
# ---------------------------------------------------------------------------

_HARDCODED_TIER_MAP: dict[str, dict[str, Any]] = {
    "fast": {
        "model_id": "claude-3-5-haiku-latest",
        "engine": "cloud",
        "context_length": 200000,
        "cost_per_1k_input": 1.00,
        "cost_per_1k_output": 5.00,
        "fallback": "llama3.2:3b",
    },
    "auto": {
        "model_id": "claude-3-5-haiku-latest",
        "engine": "cloud",
        "context_length": 200000,
        "cost_per_1k_input": 1.00,
        "cost_per_1k_output": 5.00,
        "fallback": "llama3.2:3b",
    },
    "smart": {
        "model_id": "claude-sonnet-4-20250514",
        "engine": "cloud",
        "context_length": 200000,
        "cost_per_1k_input": 3.00,
        "cost_per_1k_output": 15.00,
        "fallback": "qwen3:8b",
    },
    "primary": {
        "model_id": "claude-sonnet-4-20250514",
        "engine": "cloud",
        "context_length": 200000,
        "cost_per_1k_input": 3.00,
        "cost_per_1k_output": 15.00,
        "fallback": "qwen3:8b",
    },
    "genius": {
        "model_id": "claude-opus-4-20250514",
        "engine": "cloud",
        "context_length": 200000,
        "cost_per_1k_input": 15.00,
        "cost_per_1k_output": 75.00,
        "fallback": "claude-sonnet-4-20250514",
    },
    "local": {
        "model_id": "qwen2.5:14b-instruct-q4_K_M",
        "engine": "ollama",
        "context_length": 32768,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
        "fallback": "llama3.2:3b",
    },
    "local-small": {
        "model_id": "llama3.2:3b",
        "engine": "ollama",
        "context_length": 131072,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
        "fallback": "llama3.2:3b",
    },
}

# ---------------------------------------------------------------------------
# Preferred catalog models per tier (ordered by preference)
# ---------------------------------------------------------------------------

_CATALOG_TIER_PREFERENCES: dict[str, list[str]] = {
    "fast": [
        "claude-haiku-4-5",
        "claude-3-5-haiku-latest",
        "gemini-2.5-flash",
        "gpt-4o-mini",
    ],
    "auto": [
        "claude-haiku-4-5",
        "claude-3-5-haiku-latest",
        "gemini-2.5-flash",
    ],
    "smart": [
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-6",
        "gemini-2.5-pro",
        "gpt-4o",
    ],
    "primary": [
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-6",
    ],
    "genius": [
        "claude-opus-4-20250514",
        "claude-opus-4-6",
        "gemini-3-pro",
    ],
    "local": [
        "qwen3:8b",
        "qwen3.5:14b",
        "granite3.3:8b",
        "qwen3.5:8b",
        "mistral:7b",
    ],
    "local-small": [
        "llama3.2:3b",
        "qwen3.5:3b",
        "granite4.0-micro",
        "qwen3.5:4b",
    ],
}

# Budget-constrained downgrade tiers
_BUDGET_DOWNGRADE_80: dict[str, str] = {
    "fast": "local-small",
    "auto": "local-small",
    "smart": "fast",
    "primary": "fast",
    "genius": "smart",
    "local": "local",
    "local-small": "local-small",
}

_BUDGET_DOWNGRADE_100: dict[str, str] = {
    "fast": "local-small",
    "auto": "local-small",
    "smart": "local",
    "primary": "local",
    "genius": "local",
    "local": "local",
    "local-small": "local-small",
}

# Task-type hints for model selection (Phase 30: added verifiable + pass_k_eligible)
_TASK_TYPE_HINTS: dict[str, dict[str, Any]] = {
    "email": {"prefer_fast": True, "min_context": 4096, "verifiable": False, "pass_k_eligible": False},
    "email_subject": {"prefer_fast": True, "min_context": 2048, "verifiable": True, "pass_k_eligible": True, "verifier": "length_and_format"},
    "research": {"prefer_smart": True, "min_context": 32000, "verifiable": False, "pass_k_eligible": False},
    "code": {"prefer_smart": True, "min_context": 16000, "verifiable": True, "pass_k_eligible": True, "verifier": "syntax_check"},
    "classification": {"prefer_fast": True, "min_context": 2048, "verifiable": True, "pass_k_eligible": True, "verifier": "label_in_set"},
    "extraction": {"prefer_fast": True, "min_context": 4096, "verifiable": True, "pass_k_eligible": True, "verifier": "json_schema_valid"},
    "lead_scoring": {"prefer_fast": True, "min_context": 2048, "verifiable": True, "pass_k_eligible": True, "verifier": "score_in_range"},
    "summarization": {"prefer_fast": True, "min_context": 8000, "verifiable": False, "pass_k_eligible": False},
    "proposal": {"prefer_smart": True, "min_context": 8000, "verifiable": False, "pass_k_eligible": False},
    "architecture": {"prefer_smart": True, "min_context": 16000, "verifiable": False, "pass_k_eligible": False},
    "general": {"min_context": 4096, "verifiable": False, "pass_k_eligible": False},
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ModelSelection:
    """Result of model selection."""

    model_id: str
    engine: str
    tier: str
    reason: str
    estimated_cost: float
    context_length: int
    fallback_model: str


# ---------------------------------------------------------------------------
# Phase 30: T2 Selection (pass@k for verifiable tasks)
# ---------------------------------------------------------------------------

# Max passes to bound latency and cost
T2_MAX_PASSES = int(os.environ.get("T2_MAX_PASSES", "5"))


@dataclass
class T2Selection:
    """T2-informed model selection with pass@k strategy.

    Research: T2 Scaling Laws (arxiv:2604.01411)

    When a task has a verifier (automated quality check), it may be cheaper
    to run a smaller model k times than a large model once.
    """

    model: str
    passes: int = 1
    selection_strategy: str = "best"  # "best" | "majority" | "first_passing"
    verifier: str | None = None


# T2 decision matrix: (task_type, tier) -> (downgraded_tier, passes)
_T2_PASS_K_CONFIG: dict[str, dict[str, tuple[str, int, str]]] = {
    # task_type -> {original_tier -> (use_tier, passes, strategy)}
    "classification": {
        "smart": ("fast", 3, "majority"),
        "primary": ("fast", 3, "majority"),
    },
    "extraction": {
        "smart": ("fast", 3, "best"),
        "primary": ("fast", 3, "best"),
    },
    "email_subject": {
        "smart": ("fast", 5, "best"),
        "primary": ("fast", 5, "best"),
    },
    "lead_scoring": {
        "smart": ("fast", 3, "best"),
        "primary": ("fast", 3, "best"),
    },
    "code": {
        "genius": ("smart", 2, "best"),
    },
}


# ---------------------------------------------------------------------------
# Core selection logic
# ---------------------------------------------------------------------------


def _estimate_call_cost(spec_or_hardcoded: Any, is_catalog: bool = False) -> float:
    """Estimate per-call cost in USD (assumes ~1K input + 0.5K output tokens)."""
    if is_catalog and hasattr(spec_or_hardcoded, "metadata"):
        meta = spec_or_hardcoded.metadata
        price_in = meta.get("pricing_input", 0.0)
        price_out = meta.get("pricing_output", 0.0)
        # pricing is per 1M tokens; estimate for 1K in + 0.5K out
        return (price_in * 1.0 / 1000) + (price_out * 0.5 / 1000)
    elif isinstance(spec_or_hardcoded, dict):
        price_in = spec_or_hardcoded.get("cost_per_1k_input", 0.0)
        price_out = spec_or_hardcoded.get("cost_per_1k_output", 0.0)
        # per-1M pricing; same estimate
        return (price_in * 1.0 / 1000) + (price_out * 0.5 / 1000)
    return 0.0


def _resolve_tier_for_budget(tier: str, budget_remaining_pct: float) -> tuple[str, str]:
    """Apply budget-aware tier downgrade. Returns (resolved_tier, reason_fragment)."""
    if budget_remaining_pct >= 80.0:
        return tier, ""
    elif budget_remaining_pct > 0.0:
        resolved = _BUDGET_DOWNGRADE_80.get(tier, tier)
        if resolved != tier:
            return resolved, f"downgraded from {tier} (budget at {budget_remaining_pct:.0f}%)"
        return tier, ""
    else:
        resolved = _BUDGET_DOWNGRADE_100.get(tier, "local")
        if resolved != tier:
            return resolved, f"downgraded from {tier} (budget exhausted)"
        return tier, ""


def _select_from_catalog(
    tier: str,
    required_context: int = 0,
    prefer_local: bool = False,
) -> Optional[tuple[Any, str]]:
    """Try to find the best model from the OJ Intelligence catalog.

    Returns (ModelSpec, engine_key) or None if catalog is unavailable.
    """
    if not _OJ_CATALOG_AVAILABLE or _ModelRegistry is None:
        return None

    effective_tier = tier
    if prefer_local and tier not in ("local", "local-small"):
        effective_tier = "local"

    preferences = _CATALOG_TIER_PREFERENCES.get(effective_tier, [])

    for model_id in preferences:
        if _ModelRegistry.contains(model_id):
            spec = _ModelRegistry.get(model_id)
            # Check context length requirement
            if required_context > 0 and spec.context_length < required_context:
                continue
            # Determine engine
            engine = "ollama"
            if "cloud" in spec.supported_engines:
                engine = "cloud"
            elif "ollama" in spec.supported_engines:
                engine = "ollama"
            elif "vllm" in spec.supported_engines:
                engine = "vllm"
            elif spec.supported_engines:
                engine = spec.supported_engines[0]
            return spec, engine

    return None


def _select_fallback_model(tier: str) -> str:
    """Get the fallback model ID for a given tier."""
    hardcoded = _HARDCODED_TIER_MAP.get(tier, _HARDCODED_TIER_MAP["local"])
    return hardcoded["fallback"]


async def select_model(
    tier: str,
    task_type: str = "general",
    budget_remaining_pct: float = 100.0,
    required_context: int = 0,
    prefer_local: bool = False,
) -> ModelSelection:
    """Select the best model for a task based on tier, budget, and requirements.

    Args:
        tier: "fast", "smart", "genius", "local", "local-small"
        task_type: "email", "research", "code", "classification", etc.
        budget_remaining_pct: current budget remaining (0-100+)
        required_context: minimum context window needed
        prefer_local: force local model selection

    Returns:
        ModelSelection with the chosen model and metadata.
    """
    if tier not in _HARDCODED_TIER_MAP:
        tier = "fast"

    # Apply task-type context requirements
    task_hints = _TASK_TYPE_HINTS.get(task_type, _TASK_TYPE_HINTS["general"])
    effective_context = max(required_context, task_hints.get("min_context", 0))

    # Force local if requested
    if prefer_local and tier not in ("local", "local-small"):
        tier = "local"

    # Apply budget-aware downgrade
    resolved_tier, budget_reason = _resolve_tier_for_budget(tier, budget_remaining_pct)

    # Try catalog-driven selection first
    catalog_result = _select_from_catalog(
        resolved_tier,
        required_context=effective_context,
        prefer_local=prefer_local,
    )

    if catalog_result is not None:
        spec, engine = catalog_result
        cost = _estimate_call_cost(spec, is_catalog=True)
        reason_parts = [f"catalog: {spec.name} ({spec.provider})"]
        if budget_reason:
            reason_parts.append(budget_reason)
        if task_type != "general":
            reason_parts.append(f"task={task_type}")

        fallback = _select_fallback_model(resolved_tier)

        return ModelSelection(
            model_id=spec.model_id,
            engine=engine,
            tier=resolved_tier,
            reason="; ".join(reason_parts),
            estimated_cost=cost,
            context_length=spec.context_length,
            fallback_model=fallback,
        )

    # Fall back to hardcoded mappings
    hardcoded = _HARDCODED_TIER_MAP.get(resolved_tier, _HARDCODED_TIER_MAP["fast"])
    cost = _estimate_call_cost(hardcoded, is_catalog=False)

    reason_parts = [f"hardcoded: {hardcoded['model_id']}"]
    if budget_reason:
        reason_parts.append(budget_reason)
    if not _OJ_CATALOG_AVAILABLE:
        reason_parts.append("OJ catalog unavailable")

    return ModelSelection(
        model_id=hardcoded["model_id"],
        engine=hardcoded["engine"],
        tier=resolved_tier,
        reason="; ".join(reason_parts),
        estimated_cost=cost,
        context_length=hardcoded["context_length"],
        fallback_model=hardcoded["fallback"],
    )


# ---------------------------------------------------------------------------
# Catalog introspection helpers
# ---------------------------------------------------------------------------


def get_model_specs() -> dict[str, Any]:
    """Get all available model specs from OJ Intelligence catalog.

    Returns a dict mapping model_id -> ModelSpec (or hardcoded dict if
    catalog is unavailable).
    """
    if _OJ_CATALOG_AVAILABLE:
        result = {}
        for spec in _oj_builtin_models:
            result[spec.model_id] = spec
        return result

    # Fallback: return hardcoded tier map as simplified specs
    return {
        info["model_id"]: {
            "model_id": info["model_id"],
            "engine": info["engine"],
            "context_length": info["context_length"],
            "cost_per_1k_input": info["cost_per_1k_input"],
            "cost_per_1k_output": info["cost_per_1k_output"],
        }
        for tier, info in _HARDCODED_TIER_MAP.items()
    }


def get_available_models(engine_type: Optional[str] = None) -> list[str]:
    """List models available on currently running engines.

    Args:
        engine_type: Filter by engine ("cloud", "ollama", "vllm", etc.).
                     None returns all models.

    Returns:
        List of model_id strings.
    """
    if _OJ_CATALOG_AVAILABLE:
        result = []
        for spec in _oj_builtin_models:
            if engine_type is None:
                result.append(spec.model_id)
            elif engine_type in spec.supported_engines:
                result.append(spec.model_id)
        return result

    # Fallback: return hardcoded model IDs
    seen = set()
    result = []
    for info in _HARDCODED_TIER_MAP.values():
        mid = info["model_id"]
        if mid not in seen:
            if engine_type is None or info["engine"] == engine_type:
                seen.add(mid)
                result.append(mid)
    return result


def is_catalog_available() -> bool:
    """Check whether the OJ Intelligence catalog is loaded."""
    return _OJ_CATALOG_AVAILABLE


# ---------------------------------------------------------------------------
# Phase 30: T2 model selection entry point
# ---------------------------------------------------------------------------


def _t2_enabled() -> bool:
    """Check if the T2_MODEL_SELECT feature flag is active."""
    return os.environ.get("T2_MODEL_SELECT", "true").lower() in ("1", "true", "yes")


async def select_model_t2(
    tier: str,
    task_type: str = "general",
    budget_remaining_pct: float = 100.0,
    required_context: int = 0,
    prefer_local: bool = False,
) -> tuple[ModelSelection, T2Selection]:
    """Select a model with T2 pass@k optimization for verifiable tasks.

    Returns (base_selection, t2_selection).
    When T2 is disabled or the task is not verifiable, t2_selection has passes=1.
    """
    base = await select_model(
        tier=tier,
        task_type=task_type,
        budget_remaining_pct=budget_remaining_pct,
        required_context=required_context,
        prefer_local=prefer_local,
    )

    # Default: single pass
    t2 = T2Selection(model=base.model_id, passes=1)

    if not _t2_enabled():
        return base, t2

    # Check if the task is verifiable and pass@k eligible
    hints = _TASK_TYPE_HINTS.get(task_type, _TASK_TYPE_HINTS["general"])
    if not hints.get("pass_k_eligible", False):
        return base, t2

    verifier = hints.get("verifier")
    if not verifier:
        return base, t2

    # Look up T2 pass@k config for this task+tier
    t2_config = _T2_PASS_K_CONFIG.get(task_type, {}).get(tier)
    if t2_config is None:
        # No T2 optimization for this combination
        t2.verifier = verifier
        return base, t2

    use_tier, passes, strategy = t2_config
    passes = min(passes, T2_MAX_PASSES)

    # Select model at the downgraded tier
    downgraded = await select_model(
        tier=use_tier,
        task_type=task_type,
        budget_remaining_pct=budget_remaining_pct,
        required_context=required_context,
        prefer_local=prefer_local,
    )

    t2 = T2Selection(
        model=downgraded.model_id,
        passes=passes,
        selection_strategy=strategy,
        verifier=verifier,
    )

    logger.info(
        "T2 selection: %s x%d (%s) for %s (was %s x1)",
        downgraded.model_id, passes, strategy, task_type, base.model_id,
    )

    return downgraded, t2


async def execute_with_t2(
    t2: T2Selection,
    generate_fn,
    verify_fn=None,
) -> list[Any]:
    """Execute pass@k candidates concurrently using T2 selection.

    Args:
        t2: T2Selection from select_model_t2
        generate_fn: async callable that produces one candidate
        verify_fn: optional async callable(candidate) -> float score (0-1).
                   If None, all candidates are returned.

    Returns list of candidates sorted by verification score (best first).
    Falls back to single Opus call if no candidate passes.
    """
    import asyncio

    if t2.passes <= 1:
        result = await generate_fn()
        return [result]

    # Generate k candidates concurrently
    tasks = [generate_fn() for _ in range(t2.passes)]
    candidates = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions
    valid = [c for c in candidates if not isinstance(c, BaseException)]
    if not valid:
        # All failed — caller should handle fallback
        return []

    if verify_fn is None:
        return valid

    # Score each candidate
    scored: list[tuple[float, Any]] = []
    for candidate in valid:
        try:
            score = await verify_fn(candidate)
            scored.append((score, candidate))
        except (ValueError, TypeError, OSError):
            scored.append((0.0, candidate))

    if not scored:
        return valid

    # Select based on strategy
    if t2.selection_strategy == "first_passing":
        passing = [(s, c) for s, c in scored if s > 0.5]
        if passing:
            return [passing[0][1]]
        return [scored[0][1]]  # best effort
    elif t2.selection_strategy == "majority":
        # Return the most common result (by string repr for simplicity)
        from collections import Counter
        reprs = Counter(str(c) for _, c in scored)
        majority_repr = reprs.most_common(1)[0][0]
        for _, c in scored:
            if str(c) == majority_repr:
                return [c]
    # Default: "best" — sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]
