"""Ollama local inference provider for the unified LLM factory.

Extracts Ollama API logic from shared/llm_client.py. Preserves TurboQuant
KV cache options from config.ollama.kv_cache_type and flash_attention.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from shared.llm_unified import LLMResponse, ModelTier

logger = logging.getLogger("perseus.llm.providers.ollama")


class OllamaProvider:
    """Ollama local inference provider with TurboQuant KV cache support."""

    def __init__(self) -> None:
        self._http: Any = None

    def _ensure_client(self) -> Any:
        """Lazily create httpx.AsyncClient."""
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            )
        return self._http

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model_id: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Call Ollama /api/generate endpoint."""
        from shared.config import config

        client = self._ensure_client()
        t0 = time.perf_counter()

        # Resolve model_id if empty (tier-based resolution)
        if not model_id:
            model_id = config.ollama.model

        options: dict[str, Any] = {
            "num_predict": max_tokens,
            "temperature": temperature,
        }

        # TurboQuant KV cache compression
        if config.ollama.kv_cache_type:
            options["cache_type_k"] = config.ollama.kv_cache_type
            options["cache_type_v"] = config.ollama.kv_cache_type
        if config.ollama.flash_attention:
            options["flash_attention"] = True

        body: dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            body["system"] = system

        host = config.ollama.host
        resp = await client.post(f"{host}/api/generate", json=body)

        # Model not found -- fall back to secondary model
        if resp.status_code == 404 and model_id != config.ollama.secondary:
            logger.warning(
                "Ollama model %s not found, falling back to %s",
                model_id,
                config.ollama.secondary,
            )
            body["model"] = config.ollama.secondary
            model_id = config.ollama.secondary
            resp = await client.post(f"{host}/api/generate", json=body)

        resp.raise_for_status()
        data = resp.json()
        latency_ms = int((time.perf_counter() - t0) * 1000)

        content = data.get("response", "")

        # Ollama may provide eval_count and prompt_eval_count
        input_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)

        return LLMResponse(
            content=content,
            model_id=model_id,
            engine="ollama",
            tier_requested=ModelTier.LOCAL,
            tier_actual=ModelTier.LOCAL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,  # Local inference is free
            latency_ms=latency_ms,
        )

    async def health_check(self) -> bool:
        """Ping Ollama /api/tags endpoint."""
        try:
            from shared.config import config

            client = self._ensure_client()
            resp = await client.get(f"{config.ollama.host}/api/tags")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Release HTTP client resources."""
        if self._http is not None and hasattr(self._http, "aclose"):
            await self._http.aclose()
            self._http = None
