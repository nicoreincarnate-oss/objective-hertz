"""
Unified LLM client for Perseus.
Primary: Claude API (via Anthropic SDK) — high quality for emails, decisions, proposals.
Fallback: Ollama (local) — for simple classification, extraction, when API is down or budget is tight.

Budget-aware: every Claude call estimates token cost and records it.
When budget hits alert threshold, auto-downgrades to Ollama.
When budget is exceeded, only Ollama is available.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

from shared.config import config

logger = logging.getLogger("perseus.llm")

# Approximate cost per 1K tokens (input + output blended) as of March 2026
# Conservative estimates — better to overcount than undercount
_COST_PER_1K = {
    "haiku": 0.001,    # ~$0.25/M input + $1.25/M output blended
    "sonnet": 0.006,   # ~$3/M input + $15/M output blended
}


class LLMClient:
    """Unified interface to Claude API + Ollama. Budget-aware."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate text. Model choices:
        - "auto": local-first path for high-throughput work
        - "fast": Ollama primary model (cheap, local, default fast path)
        - "smart": Claude Sonnet (best quality)
        - "fast-remote": Claude Haiku (cheap remote fallback)
        - "local": Ollama primary model (free)
        - "local-small": Ollama secondary model (free)

        Budget behavior:
        - Over alert threshold (80%): "auto" and "fast" downgrade to Ollama
        - Budget exceeded: all Claude calls downgrade to Ollama
        - "smart" downgrades only when budget is fully exceeded
        """
        if model == "auto":
            model = "fast"

        # Budget check — downgrade Claude to Ollama when needed
        if model in ("fast-remote", "smart"):
            model = await self._budget_gate(model)

        if model in ("fast-remote", "smart"):
            try:
                result = await self._claude_generate(prompt, system, model, max_tokens, temperature)
                await self._record_claude_spend(prompt, result, system, "fast" if model == "fast-remote" else model)
                return result
            except Exception as e:
                logger.warning(f"Claude API failed, falling back to Ollama: {e}")
                return await self._ollama_generate(prompt, system, "local", max_tokens, temperature)
        elif model in ("fast", "local", "local-small"):
            return await self._ollama_generate(prompt, system, model, max_tokens, temperature)
        else:
            try:
                result = await self._claude_generate(prompt, system, "fast-remote", max_tokens, temperature)
                await self._record_claude_spend(prompt, result, system, "fast")
                return result
            except Exception as e:
                logger.warning(f"Claude API failed, falling back to Ollama: {e}")
                return await self._ollama_generate(prompt, system, "local", max_tokens, temperature)

    async def _budget_gate(self, requested_model: str) -> str:
        """Check budget and downgrade Claude to Ollama if needed."""
        try:
            from shared.db import fetch_val
            month = date.today().replace(day=1)
            total = await fetch_val(
                "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
                (month,),
            ) or 0

            cap = config.budget.monthly_cap
            percent_used = float(total) / cap if cap > 0 else 1.0

            if percent_used >= 1.0:
                # Budget exceeded — everything goes to Ollama
                logger.warning("Budget exceeded — forcing Ollama for all LLM calls")
                return "local"

            if percent_used >= config.budget.alert_threshold and requested_model == "fast-remote":
                # Over alert threshold — downgrade cheap calls to Ollama, keep "smart" on Claude
                logger.info(f"Budget at {percent_used*100:.0f}% — downgrading fast calls to Ollama")
                return "local"

        except Exception as e:
            # If we can't check budget, allow the call (fail open, not closed)
            logger.debug(f"Budget check failed (allowing call): {e}")

        return requested_model

    async def _record_claude_spend(self, prompt: str, result: str, system: str, model: str):
        """Estimate and record the cost of a Claude API call."""
        try:
            from shared.db import execute

            # Rough token estimate: ~4 chars per token
            input_tokens = (len(prompt) + len(system)) / 4
            output_tokens = len(result) / 4
            total_tokens = input_tokens + output_tokens

            tier = "haiku" if model == "fast" else "sonnet"
            cost = (total_tokens / 1000) * _COST_PER_1K[tier]

            # Only record if cost is meaningful (>$0.001)
            if cost >= 0.001:
                month = date.today().replace(day=1)
                await execute(
                    """INSERT INTO budget_tracking (month, category, amount, description)
                       VALUES (%s, 'claude_api', %s, %s)""",
                    (month, round(cost, 4), f"claude-{tier} ~{int(total_tokens)}tok"),
                )
        except Exception as e:
            logger.debug(f"Spend recording failed (non-critical): {e}")

    async def _claude_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float
    ) -> str:
        """Call Claude API via Anthropic messages endpoint."""
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = config.claude.fast_model if model == "fast-remote" else config.claude.primary_model
        messages = [{"role": "user", "content": prompt}]

        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        resp = await self._http.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "x-api-key": config.claude.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        # Use actual token counts from API response if available
        usage = data.get("usage", {})
        if usage:
            self._last_usage = usage  # Cache for more accurate spend recording

        return data["content"][0]["text"]

    async def _ollama_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float
    ) -> str:
        """Call local Ollama API."""
        model_name = config.ollama.model if model == "local" else config.ollama.secondary
        body = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            body["system"] = system

        resp = await self._http.post(f"{config.ollama.host}/api/generate", json=body)
        resp.raise_for_status()
        return resp.json()["response"]

    async def classify(self, text: str, categories: list[str]) -> str:
        """Quick classification using fast model."""
        cats = ", ".join(categories)
        prompt = f"Classify this text into exactly one category: [{cats}]\n\nText: {text}\n\nCategory:"
        result = await self.generate(prompt, model="local-small", max_tokens=50, temperature=0.0)
        # Extract the category from the response
        result = result.strip().strip('"').strip("'")
        for cat in categories:
            if cat.lower() in result.lower():
                return cat
        return categories[0]  # default to first category

    async def embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama nomic-embed-text (free, local)."""
        resp = await self._http.post(
            f"{config.ollama.host}/api/embeddings",
            json={"model": config.ollama.embed_model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    async def close(self):
        await self._http.aclose()


# Singleton
llm = LLMClient()
