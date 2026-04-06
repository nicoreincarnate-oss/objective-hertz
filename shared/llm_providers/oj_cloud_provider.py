"""OpenJarvis CloudEngine wrapper for the unified LLM factory.

Thin wrapper around openjarvis.engine.cloud.CloudEngine that satisfies
LLMProvider protocol. Enables OpenAI/Google/MiniMax/OpenRouter access
via the unified factory for the OJ agent path.

Lazily imports CloudEngine to avoid import-time side effects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from shared.llm_unified import LLMResponse, resolve_tier

logger = logging.getLogger("perseus.llm.providers.oj_cloud")


class OJCloudProvider:
    """Multi-provider wrapper around OpenJarvis CloudEngine."""

    def __init__(self) -> None:
        self._engine: Any = None

    def _ensure_engine(self) -> Any:
        """Lazily import and instantiate CloudEngine."""
        if self._engine is None:
            from openjarvis.engine.cloud import CloudEngine

            self._engine = CloudEngine()
        return self._engine

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model_id: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Generate via CloudEngine, converting result to LLMResponse."""
        engine = self._ensure_engine()
        t0 = time.perf_counter()

        # Build messages for CloudEngine (expects Message objects or dicts)
        from openjarvis.core.types import Message, MessageRole

        messages: list[Message] = []
        if system:
            messages.append(Message(role=MessageRole.SYSTEM, content=system))
        messages.append(Message(role=MessageRole.USER, content=prompt))

        # CloudEngine.generate is sync -- run in thread to not block event loop
        result_dict = await asyncio.to_thread(
            engine.generate,
            messages,
            model=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)

        content = result_dict.get("content", "")
        usage = result_dict.get("usage", {})
        cost_usd = result_dict.get("cost_usd", 0.0)
        actual_model = result_dict.get("model", model_id)

        tier = resolve_tier(self._model_id_to_tier_str(actual_model))

        return LLMResponse(
            content=content,
            model_id=actual_model,
            engine="oj_cloud",
            tier_requested=tier,
            tier_actual=tier,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            raw_usage=usage,
        )

    @staticmethod
    def _model_id_to_tier_str(model_id: str) -> str:
        """Map a model ID to a tier string for cost estimation."""
        model_lower = model_id.lower()
        if "opus" in model_lower or "gpt-4o" in model_lower:
            return "genius"
        if "haiku" in model_lower or "gpt-4o-mini" in model_lower:
            return "fast"
        return "smart"

    async def health_check(self) -> bool:
        """Check if CloudEngine can be instantiated."""
        try:
            self._ensure_engine()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Release CloudEngine resources."""
        if self._engine is not None:
            if hasattr(self._engine, "close"):
                self._engine.close()
            self._engine = None
