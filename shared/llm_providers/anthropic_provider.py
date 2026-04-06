"""Anthropic (Claude) provider for the unified LLM factory.

Extracts Claude API logic from shared/llm_client.py into a standalone provider
that satisfies the LLMProvider protocol. Key improvements over the original:

- Returns LLMResponse with real token counts from API usage field (Phase 17)
- Strips thinking blocks from extended-thinking responses (B-14 artifact cleanup)
- Strips tool_use blocks when caller did not request tools (B-14)
- Lazily imports httpx -- resolved once at first call
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from shared.llm_unified import LLMResponse, ModelTier, resolve_tier

logger = logging.getLogger("perseus.llm.providers.anthropic")

# Approximate cost per 1K tokens (input + output blended) as of April 2026
_COST_PER_1K = {
    "haiku": 0.001,
    "sonnet": 0.006,
    "opus": 0.045,
}


def _tier_to_cost_key(tier: ModelTier) -> str:
    """Map ModelTier to cost table key."""
    if tier == ModelTier.GENIUS:
        return "opus"
    if tier == ModelTier.FAST:
        return "haiku"
    return "sonnet"


class AnthropicProvider:
    """Claude API provider -- lazily imports SDK, caches HTTP client."""

    def __init__(self) -> None:
        self._http: Any = None
        self._resolved = False

    def _ensure_client(self) -> Any:
        """Lazily create httpx.AsyncClient for API calls."""
        if not self._resolved:
            import httpx

            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=10.0)
            )
            self._resolved = True
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
        """Call Claude API and return structured LLMResponse."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = self._ensure_client()
        t0 = time.perf_counter()

        messages = [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        # Model-specific request timeout
        _MODEL_TIMEOUTS = {"opus": 180.0, "haiku": 60.0}
        tier_key = ""
        for k in _MODEL_TIMEOUTS:
            if k in model_id.lower():
                tier_key = k
                break
        request_timeout = _MODEL_TIMEOUTS.get(tier_key, 120.0)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers=headers,
            timeout=request_timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Extract real token counts from API response
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        # Clean response content (strip thinking blocks, unwanted tool_use)
        content_blocks = data.get("content", [])
        content = self._clean_response_blocks(content_blocks, tools_requested=False)

        if not content:
            raise RuntimeError("Empty response from Claude API")

        # Estimate cost using real token counts
        tier = resolve_tier(self._model_id_to_tier_str(model_id))
        cost_key = _tier_to_cost_key(tier)
        total_tokens = input_tokens + output_tokens
        cost_usd = (total_tokens / 1000) * _COST_PER_1K.get(cost_key, 0.006)

        return LLMResponse(
            content=content,
            model_id=model_id,
            engine="anthropic",
            tier_requested=tier,
            tier_actual=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            raw_usage=usage,
        )

    def _clean_response_blocks(
        self, content_blocks: list, tools_requested: bool
    ) -> str:
        """Remove model-specific artifacts from response content.

        - Extended thinking: filter out type="thinking" blocks
        - Tool use: filter out type="tool_use" blocks when caller did not pass tools
        - Concatenate remaining text blocks
        """
        clean_parts: list[str] = []
        for block in content_blocks:
            block_type = (
                getattr(block, "type", None)
                or (block.get("type") if isinstance(block, dict) else None)
            )

            # Skip thinking blocks (extended thinking artifact from Opus)
            if block_type == "thinking":
                thinking_text = (
                    getattr(block, "thinking", "")
                    or (
                        block.get("thinking", "")
                        if isinstance(block, dict)
                        else ""
                    )
                )
                logger.debug(
                    "Stripped thinking block (%d chars)", len(str(thinking_text))
                )
                continue

            # Skip tool_use blocks when caller didn't request tools
            if block_type == "tool_use" and not tools_requested:
                tool_name = (
                    getattr(block, "name", "unknown")
                    or (
                        block.get("name", "unknown")
                        if isinstance(block, dict)
                        else "unknown"
                    )
                )
                logger.debug("Stripped unrequested tool_use block: %s", tool_name)
                continue

            # Keep text blocks
            if block_type == "text":
                text = (
                    getattr(block, "text", "")
                    or (block.get("text", "") if isinstance(block, dict) else "")
                )
                if text:
                    clean_parts.append(text)

        return "\n".join(clean_parts) if clean_parts else ""

    @staticmethod
    def _model_id_to_tier_str(model_id: str) -> str:
        """Map a model ID string to a tier string."""
        model_lower = model_id.lower()
        if "opus" in model_lower:
            return "genius"
        if "haiku" in model_lower:
            return "fast"
        return "smart"

    async def health_check(self) -> bool:
        """Check if Anthropic API is likely available."""
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def close(self) -> None:
        """Release HTTP client resources."""
        if self._http is not None and hasattr(self._http, "aclose"):
            await self._http.aclose()
            self._http = None
            self._resolved = False
