"""Unified LLM Factory -- single entry point for all LLM inference.

Phase 23: Dispatches to providers via fallback chains. Replaces the split-brain
pattern of separate LLMClient + CloudEngine + LLMEngineAdapter paths.

This module is ONLY active when ANATOMY_UNIFIED_LLM=true. When false,
the existing LLMClient singleton is used unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from shared.llm_unified import (
    DEFAULT_CHAINS,
    FallbackChain,
    FallbackStep,
    LLMResponse,
    ModelTier,
    resolve_tier,
)

logger = logging.getLogger("perseus.llm.factory")


class UnifiedLLMFactory:
    """Single factory for all LLM inference. Dispatches by tier with graduated fallback."""

    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._chains: dict[ModelTier, FallbackChain] = dict(DEFAULT_CHAINS)
        self._shadow_mode: bool = False
        self._shadow_comparator: Any | None = None
        self._withholding_enabled: bool = os.environ.get(
            "UNIFIED_LLM_WITHHOLDING", "true"
        ).lower() in ("true", "1")
        self._providers_initialized: bool = False
        self._chain_overrides_loaded: bool = False

    def _ensure_providers(self) -> None:
        """Lazily initialize providers. Called once on first generate()."""
        if self._providers_initialized:
            return
        self._providers_initialized = True

        # Anthropic -- if ANTHROPIC_API_KEY is set
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from shared.llm_providers.anthropic_provider import AnthropicProvider

                self._providers["anthropic"] = AnthropicProvider()
            except ImportError:
                logger.debug("AnthropicProvider unavailable")

        # Ollama -- always available (local)
        try:
            from shared.llm_providers.ollama_provider import OllamaProvider

            self._providers["ollama"] = OllamaProvider()
        except ImportError:
            logger.debug("OllamaProvider unavailable")

        # Heavy local -- if AirLLM or oLLM is configured
        try:
            from shared.config import config as app_config

            if getattr(app_config, "airllm", None) and getattr(
                app_config.airllm, "enabled", False
            ):
                from shared.llm_providers.heavy_local_provider import HeavyLocalProvider

                self._providers["heavy_local"] = HeavyLocalProvider()
            elif getattr(app_config, "ollm", None) and getattr(
                app_config.ollm, "enabled", False
            ):
                from shared.llm_providers.heavy_local_provider import HeavyLocalProvider

                self._providers["heavy_local"] = HeavyLocalProvider()
        except (ImportError, AttributeError):
            logger.debug("HeavyLocalProvider unavailable")

        # OJ Cloud -- if any non-Anthropic API key is set
        if any(
            os.environ.get(k)
            for k in (
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "OPENROUTER_API_KEY",
                "MINIMAX_API_KEY",
            )
        ):
            try:
                from shared.llm_providers.oj_cloud_provider import OJCloudProvider

                self._providers["oj_cloud"] = OJCloudProvider()
            except ImportError:
                logger.debug("OJCloudProvider unavailable")

    async def _load_chain_overrides(self) -> None:
        """Load fallback chain overrides from DB config.

        Expected format in system_config.value (JSON):
        {
          "genius": [
            {"tier": "genius", "model_id": "claude-opus-4-6", "engine": "anthropic", "timeout": 120},
            ...
          ]
        }
        """
        if self._chain_overrides_loaded:
            return
        self._chain_overrides_loaded = True

        try:
            import json

            from shared.db import get_config

            chain_json = await get_config("llm_fallback_chains")
            if chain_json:
                overrides = json.loads(chain_json)
                for tier_str, steps_data in overrides.items():
                    tier = ModelTier(tier_str)
                    steps = [
                        FallbackStep(
                            tier=ModelTier(s["tier"]),
                            model_id=s.get("model_id", ""),
                            engine=s["engine"],
                            timeout_seconds=s.get("timeout", 120.0),
                        )
                        for s in steps_data
                    ]
                    self._chains[tier] = FallbackChain(steps)
                logger.info(
                    "Loaded fallback chain overrides for %d tiers", len(overrides)
                )
        except Exception as exc:
            logger.debug("No chain overrides loaded (using defaults): %s", exc)

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "fast",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        client_id: int | None = None,
        pipeline_stage: str = "",
        use_dna: bool = False,
        daemon_name: str = "",
    ) -> str:
        """Generate text with graduated fallback.

        This is the ONLY public generate method. All callers use this.
        Signature matches LLMClient.generate() exactly for drop-in compatibility.
        """
        self._ensure_providers()
        await self._load_chain_overrides()

        if model == "auto":
            model = "fast"

        model_tier = resolve_tier(model)

        # DNA injection (reuse existing logic)
        if use_dna and daemon_name:
            from shared.llm_client import LLMClient

            system = LLMClient._inject_dna(system, daemon_name)

        # Lifecycle injection
        if daemon_name and os.environ.get(
            "HEARTBEAT_LIFECYCLE_ENABLED", ""
        ).lower() in ("true", "1"):
            from pathlib import Path

            lifecycle_path = Path(f"soul/lifecycle/{daemon_name}_lifecycle.md")
            if lifecycle_path.exists():
                lifecycle = lifecycle_path.read_text()
                system = (
                    f"{lifecycle}\n\n---\n\n{system}" if system else lifecycle
                )

        # Budget check -- may alter the starting tier
        model_tier = await self._apply_budget_gate(model_tier, pipeline_stage)

        # Walk the fallback chain
        chain = self._chains.get(model_tier, self._chains.get(ModelTier.FAST))
        if chain is None:
            chain = DEFAULT_CHAINS[ModelTier.FAST]

        if self._withholding_enabled:
            from shared.llm_withholding import WithholdingBuffer

            buffer = WithholdingBuffer(self)
            response = await buffer.execute_chain_with_withholding(
                chain, prompt, system, max_tokens, temperature
            )
        else:
            response = await self._execute_chain(
                chain, prompt, system, max_tokens, temperature
            )

        # Record spend + metrics
        await self._record_spend(response, client_id, pipeline_stage)
        self._fire_metrics(response, pipeline_stage, daemon_name)

        # Log fallback if triggered
        if response.fallback_triggered:
            await self._log_fallback(response, pipeline_stage, daemon_name)

        return response.content

    async def _execute_chain(
        self,
        chain: FallbackChain,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Walk the fallback chain, trying each step until one succeeds."""
        last_error: Exception | None = None

        for depth, step in enumerate(chain):
            provider = self._providers.get(step.engine)
            if provider is None:
                continue

            model_id = step.model_id or await self._resolve_model_id(step.tier)

            try:
                response = await asyncio.wait_for(
                    provider.generate(
                        prompt,
                        system=system,
                        model_id=model_id,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ),
                    timeout=step.timeout_seconds,
                )
                if depth > 0:
                    response.fallback_triggered = True
                    response.fallback_reason = (
                        str(last_error)[:500]
                        if last_error
                        else "previous_unavailable"
                    )
                    response.fallback_depth = depth
                    logger.warning(
                        "Fallback triggered: %s -> %s (depth=%d, reason=%s)",
                        chain.steps[0].tier.value,
                        step.tier.value,
                        depth,
                        response.fallback_reason[:100],
                    )
                return response

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Provider %s failed for tier %s (depth %d/%d): %s",
                    step.engine,
                    step.tier.value,
                    depth + 1,
                    len(chain),
                    exc,
                )
                continue

        # All steps exhausted
        raise RuntimeError(
            f"All {len(chain)} fallback steps exhausted. Last error: {last_error}"
        )

    async def _apply_budget_gate(
        self, tier: ModelTier, pipeline_stage: str
    ) -> ModelTier:
        """Check budget and potentially downgrade tier."""
        try:
            from shared.middleware import check_budget_for_llm_call

            resolved = await check_budget_for_llm_call(tier.value)
            if resolved in ("local", "local-small"):
                return ModelTier(resolved)
            return tier
        except Exception:
            # Fail closed -- budget check error forces local
            return ModelTier.LOCAL

    async def _resolve_model_id(self, tier: ModelTier) -> str:
        """Resolve tier to concrete model ID via existing _resolve_model()."""
        from shared.llm_client import _resolve_model

        return await _resolve_model(tier.value)

    async def _record_spend(
        self,
        response: LLMResponse,
        client_id: int | None,
        pipeline_stage: str,
    ) -> None:
        """Record spend to DB (best-effort)."""
        if response.engine == "ollama" or response.cost_usd < 0.001:
            return
        try:
            from datetime import date

            from shared.db import execute

            month = date.today().replace(day=1)
            desc = f"unified-{response.engine} ~{response.input_tokens + response.output_tokens}tok"
            if pipeline_stage:
                desc = f"{pipeline_stage}: {desc}"
            await execute(
                """INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
                   VALUES (%s, 'claude_api', %s, %s, %s, %s)""",
                (
                    month,
                    round(response.cost_usd, 4),
                    desc,
                    client_id,
                    pipeline_stage or None,
                ),
            )
        except Exception as exc:
            # Budget guard fail-closed (AEGIS 1.3)
            logger.critical(
                "Spend recording DB failed -- failing closed: %s", exc
            )
            raise RuntimeError(
                f"Budget guard fail-closed: spend recording failed ({exc})"
            ) from exc

    def _fire_metrics(
        self,
        response: LLMResponse,
        pipeline_stage: str,
        daemon_name: str,
    ) -> None:
        """Fire-and-forget metrics recording."""
        try:
            from shared.observability import record_llm_call

            loop = asyncio.get_running_loop()
            loop.create_task(
                record_llm_call(
                    daemon=daemon_name or pipeline_stage or "unknown",
                    model=response.model_id,
                    call_type="generate",
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=response.latency_ms,
                    cost_usd=response.cost_usd,
                    success=True,
                    error_type=None,
                )
            )
        except RuntimeError:
            pass  # No running event loop

    async def _log_fallback(
        self, response: LLMResponse, pipeline_stage: str, daemon_name: str
    ) -> None:
        """Record fallback event to llm_fallback_log table (non-blocking)."""
        try:
            from shared.db import execute

            await execute(
                """INSERT INTO llm_fallback_log
                   (tier_requested, tier_actual, fallback_depth, fallback_reason,
                    model_id, engine, latency_ms, input_tokens, output_tokens,
                    cost_usd, pipeline_stage, daemon_name)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    response.tier_requested.value,
                    response.tier_actual.value,
                    response.fallback_depth,
                    response.fallback_reason[:500],
                    response.model_id,
                    response.engine,
                    response.latency_ms,
                    response.input_tokens,
                    response.output_tokens,
                    round(response.cost_usd, 6),
                    pipeline_stage or None,
                    daemon_name or None,
                ),
            )
        except Exception as exc:
            logger.debug("Fallback logging failed (non-critical): %s", exc)

    async def generate_with_images(
        self,
        prompt: str,
        *,
        images: list[bytes],
        system: str = "",
        model: str = "smart",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        client_id: int | None = None,
        pipeline_stage: str = "",
    ) -> str:
        """Generate text from prompt + images. Delegates to AnthropicProvider directly."""
        # Vision is only supported via Claude -- no fallback for images
        from shared.llm_client import LLMClient

        fallback = LLMClient()
        return await fallback.generate_with_images(
            prompt,
            images=images,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            client_id=client_id,
            pipeline_stage=pipeline_stage,
        )

    async def classify(self, text: str, categories: list[str]) -> str:
        """Quick classification using fast/local model."""
        if not categories:
            raise ValueError("categories list cannot be empty")
        cats = ", ".join(categories)
        prompt = f"Classify this text into exactly one category: [{cats}]\n\nText: {text}\n\nCategory:"
        result = await self.generate(prompt, model="local-small", max_tokens=50, temperature=0.0)
        result = result.strip().strip('"').strip("'")
        for cat in categories:
            if cat.lower() in result.lower():
                return cat
        return categories[0]

    async def embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama."""
        self._ensure_providers()
        provider = self._providers.get("ollama")
        if provider is None:
            raise RuntimeError("Ollama provider not available for embedding")

        from shared.config import config

        client = provider._ensure_client()
        resp = await client.post(
            f"{config.ollama.host}/api/embeddings",
            json={"model": config.ollama.embed_model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    async def close(self) -> None:
        """Release all provider resources."""
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception as exc:
                logger.debug("Error closing provider: %s", exc)
        self._providers.clear()
        self._providers_initialized = False


class UnifiedEngineAdapter:
    """Wraps an InferenceEngine, adding fallback chain + withholding to generate().

    Used by openjarvis/agents/executor.py to bring unified fallback to the OJ agent path.
    """

    def __init__(self, inner_engine: Any) -> None:
        self._inner = inner_engine

    def generate(
        self,
        messages: Any,
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Any:
        """Synchronous generate with fallback -- tries inner engine, then degrades."""
        try:
            return self._inner.generate(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except Exception as primary_exc:
            # Graduated fallback: determine next tier from model name
            fallback_model = self._next_fallback(model)
            if fallback_model and fallback_model != model:
                logger.warning(
                    "OJ engine fallback: %s -> %s (reason: %s)",
                    model,
                    fallback_model,
                    primary_exc,
                )
                return self._inner.generate(
                    messages,
                    model=fallback_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            raise

    @staticmethod
    def _next_fallback(model: str) -> str | None:
        """Determine next fallback model based on current model name."""
        if "opus" in model.lower():
            return "claude-sonnet-4-6"
        if "sonnet" in model.lower():
            return "claude-haiku-4-5"
        return None  # No further fallback for haiku/local

    def __getattr__(self, name: str) -> Any:
        """Proxy all other attributes to inner engine."""
        return getattr(self._inner, name)
