"""
Unified LLM client for Perseus.
Primary: Claude API (via Anthropic SDK) — high quality for emails, decisions, proposals.
Fallback: Ollama (local) — for simple classification, extraction, when API is down.
"""

import asyncio
import logging
from typing import Optional

import httpx

from shared.config import config

logger = logging.getLogger("perseus.llm")


class LLMClient:
    """Unified interface to Claude API + Ollama."""

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
        - "auto": Claude Haiku for speed, Sonnet if system prompt suggests complexity
        - "fast": Claude Haiku (cheap, fast)
        - "smart": Claude Sonnet (best quality)
        - "local": Ollama primary model
        - "local-small": Ollama secondary model
        """
        if model == "auto":
            model = "fast"

        if model in ("fast", "smart"):
            return await self._claude_generate(prompt, system, model, max_tokens, temperature)
        elif model in ("local", "local-small"):
            return await self._ollama_generate(prompt, system, model, max_tokens, temperature)
        else:
            # Try Claude first, fall back to Ollama
            try:
                return await self._claude_generate(prompt, system, "fast", max_tokens, temperature)
            except Exception as e:
                logger.warning(f"Claude API failed, falling back to Ollama: {e}")
                return await self._ollama_generate(prompt, system, "local", max_tokens, temperature)

    async def _claude_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float
    ) -> str:
        """Call Claude API via Anthropic messages endpoint."""
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = config.claude.fast_model if model == "fast" else config.claude.primary_model
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
        result = await self.generate(prompt, model="fast", max_tokens=50, temperature=0.0)
        # Extract the category from the response
        result = result.strip().strip('"').strip("'")
        for cat in categories:
            if cat.lower() in result.lower():
                return cat
        return categories[0]  # default to first category

    async def embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama nomic-embed-text."""
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
