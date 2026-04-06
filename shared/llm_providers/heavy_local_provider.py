"""Heavy local provider (AirLLM + oLLM) for the unified LLM factory.

Delegates backend selection to shared.airllm_policy functions:
should_route_to_heavy_local(), choose_heavy_local_backend(), explain_heavy_local_routing().

Falls back to Ollama if heavy-local backends are unavailable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from shared.llm_unified import LLMResponse, ModelTier

logger = logging.getLogger("perseus.llm.providers.heavy_local")


class HeavyLocalProvider:
    """AirLLM + oLLM provider -- routes to best available heavy-local backend."""

    def __init__(self) -> None:
        self._llm_client: Any = None

    def _ensure_llm_client(self) -> Any:
        """Lazily import LLMClient for heavy-local generation methods."""
        if self._llm_client is None:
            from shared.llm_client import LLMClient

            self._llm_client = LLMClient()
        return self._llm_client

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model_id: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Route to AirLLM or oLLM via existing LLMClient heavy-local logic."""
        t0 = time.perf_counter()
        client = self._ensure_llm_client()

        # Delegate to the existing heavy-local or Ollama generate path
        result = await client._heavy_local_or_ollama_generate(
            prompt, system, model_id or "local-heavy", max_tokens, temperature
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)

        return LLMResponse(
            content=result,
            model_id=model_id or "local-heavy",
            engine="heavy_local",
            tier_requested=ModelTier.LOCAL_HEAVY,
            tier_actual=ModelTier.LOCAL_HEAVY,
            input_tokens=len(prompt) // 4,  # Rough estimate for local
            output_tokens=len(result) // 4,
            cost_usd=0.0,
            latency_ms=latency_ms,
        )

    async def health_check(self) -> bool:
        """Check if any heavy-local backend is configured."""
        try:
            from shared.config import config

            return config.airllm.enabled or config.ollm.enabled
        except Exception:
            return False

    async def close(self) -> None:
        """Release heavy-local resources."""
        if self._llm_client is not None:
            await self._llm_client.close()
            self._llm_client = None
