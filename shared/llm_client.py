"""
Unified LLM client for Perseus.
Primary: Claude API (via Anthropic SDK) — high quality for emails, decisions, proposals.
Fallback: Ollama (local) — for simple classification, extraction, when API is down or budget is tight.

Budget enforcement lives in shared/middleware.py:check_budget_for_llm_call.
Cost recording stays here via _record_claude_spend.

TurboQuant integration (March 2026):
  Ollama calls use Google's TurboQuant KV cache compression via llama.cpp Metal.
  turbo4 = 3.8x memory reduction, near-zero accuracy loss on Apple Silicon.
  turbo3 = 4.9x memory reduction, ~1% PPL increase.
  This means larger context windows and bigger local models within 32GB RAM.
  Requires Ollama >= 0.6.2. Controlled by OLLAMA_KV_CACHE_TYPE env var.
"""

import asyncio
import base64
import logging
import os
import time
from datetime import date

import httpx

from shared.config import config
from shared.db import get_config

logger = logging.getLogger("perseus.llm")


async def _resolve_model(tier: str) -> str:
    """Resolve model ID, checking dashboard override first, then .env config."""
    _MODEL_MAP = {
        "genius": ("model_genius", config.claude.genius_model),
        "fast": ("model_fast", config.claude.fast_model),
        "smart": ("model_primary", config.claude.primary_model),
        "primary": ("model_primary", config.claude.primary_model),
        "local": ("model_local", config.ollama.model),
        "local-small": ("model_local_small", config.ollama.secondary),
        "embed": ("model_embed", config.ollama.embed_model),
    }
    db_key, fallback = _MODEL_MAP.get(tier, ("model_primary", config.claude.primary_model))
    override = await get_config(db_key, None)
    return str(override) if override else fallback

# Approximate cost per 1K tokens (input + output blended) as of March 2026
# Conservative estimates — better to overcount than undercount
_COST_PER_1K = {
    "haiku": 0.001,    # ~$0.25/M input + $1.25/M output blended
    "sonnet": 0.006,   # ~$3/M input + $15/M output blended
    "opus": 0.045,     # ~$15/M input + $75/M output blended
}


class LLMClient:
    """Unified interface to Claude API + Ollama. Budget-aware."""

    def __init__(self):
        self._http: httpx.AsyncClient | None = None
        self._last_usage = None

    def _fire_metrics(
        self,
        daemon: str,
        model: str,
        call_type: str,
        prompt: str,
        result: str,
        t0: float,
        success: bool,
        error_type: str | None,
    ) -> None:
        """Fire-and-forget LLM metrics recording via asyncio.create_task."""
        from shared.observability import record_llm_call

        latency_ms = int((time.perf_counter() - t0) * 1000)
        # Rough token estimates: ~4 chars per token
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(0, len(result) // 4)
        # Cost estimation: input * $3/M + output * $15/M (sonnet rates as default)
        cost_usd = input_tokens * 0.000003 + output_tokens * 0.000015

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                record_llm_call(
                    daemon=daemon,
                    model=model,
                    call_type=call_type,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    success=success,
                    error_type=error_type,
                )
            )
            # Per-call cost event (Phase 13 — Paperclip Pattern 8)
            from shared.cost_events import CostEvent, emit_cost_event
            from shared.observability import _task_id as _obs_task_id

            cost_event = CostEvent(
                agent_id=daemon,
                model=model,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                cached_tokens=0,  # Wire when SDK provides cached token counts
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                task_id=_obs_task_id.get() or None,
                task_type=call_type,
            )
            loop.create_task(emit_cost_event(cost_event))
        except RuntimeError:
            # No running event loop — skip metrics (e.g. during testing)
            pass

    def _get_http(self) -> httpx.AsyncClient:
        """Lazy-init the HTTP client so import alone never triggers network/SSL."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=120.0)
        return self._http

    @staticmethod
    def _inject_dna(system: str, daemon_name: str) -> str:
        """Prepend daemon DNA to the system prompt when feature flag is enabled.

        Respects the ENABLE_DNA_PROFILES env var and the circuit breaker.
        When DNA is unavailable or disabled, returns the original system string
        unchanged (zero behavior change).
        """
        try:
            from shared.agent_dna import get_circuit_breaker, get_dna

            dna_text = get_dna(daemon_name)
            if not dna_text:
                return system
            # Record success on the circuit breaker (call completed without error)
            get_circuit_breaker().record(True)
            if system:
                return f"{dna_text}\n\n---\n\n{system}"
            return dna_text
        except Exception as exc:
            logger.debug("DNA injection skipped: %s", exc)
            try:
                from shared.agent_dna import get_circuit_breaker

                get_circuit_breaker().record(False)
            except Exception:
                pass
            return system

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        client_id: int | None = None,
        pipeline_stage: str = "",
        use_dna: bool = False,
        daemon_name: str = "",
    ) -> str:
        """
        Generate text. Model choices:
        - "auto"/"fast": Claude Haiku (primary workhorse via Claude Max)
        - "smart": Claude Sonnet (best quality for proposals, strategy)
        - "genius": Claude Opus (orchestration decisions, complex reasoning)
        - "local": Ollama primary model (free, used for simple tasks)
        - "local-small": Ollama secondary model (free, classification only)

        Claude Max ($200/mo) is the primary brain. Ollama is the fallback:
        - Over alert threshold (80%): "fast" downgrades to Ollama
        - Budget exceeded: all Claude calls downgrade to Ollama
        - "smart" downgrades only when budget is fully exceeded
        - If ANTHROPIC_API_KEY is not set, everything falls back to Ollama

        DNA injection (Phase 1):
        - use_dna=True + ENABLE_DNA_PROFILES env var truthy → prepend DNA to system
        - Circuit breaker auto-disables DNA if LLM error rates spike
        """
        if model == "auto":
            model = "fast"

        # DNA injection: prepend daemon DNA to system prompt when enabled
        if use_dna and daemon_name:
            system = self._inject_dna(system, daemon_name)

        # Lifecycle injection (Phase 14): prepend lifecycle doc when flag is ON
        if daemon_name and os.environ.get("HEARTBEAT_LIFECYCLE_ENABLED", "").lower() in ("true", "1"):
            from pathlib import Path

            lifecycle_path = Path(f"soul/lifecycle/{daemon_name}_lifecycle.md")
            if lifecycle_path.exists():
                lifecycle = lifecycle_path.read_text()
                system = f"{lifecycle}\n\n---\n\n{system}" if system else lifecycle

        t0 = time.perf_counter()
        resolved_model = model

        # "fast" and "smart" both use Claude (Haiku and Sonnet respectively)
        # Only "local" and "local-small" go directly to Ollama
        if model in ("local", "local-small"):
            result = await self._ollama_generate(prompt, system, model, max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", model, "generate", prompt, result, t0, True, None)
            return result

        # If no API key, fall back to Ollama for everything
        if not config.claude.api_key:
            result = await self._ollama_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", "local", "generate", prompt, result, t0, True, None)
            return result

        # Budget check — consolidated middleware authority
        from shared.middleware import check_budget_for_llm_call
        resolved_model = await check_budget_for_llm_call(model)

        if resolved_model in ("local", "local-small"):
            # Budget gate downgraded us
            result = await self._ollama_generate(prompt, system, resolved_model, max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, result, t0, True, None)
            return result

        try:
            result = await self._claude_generate(prompt, system, resolved_model, max_tokens, temperature)
            await self._record_claude_spend(
                prompt,
                result,
                system,
                resolved_model,
                client_id=client_id,
                pipeline_stage=pipeline_stage,
            )
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, result, t0, True, None)
            return result
        except Exception as e:
            logger.warning(f"Claude API failed, falling back to Ollama: {e}")
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, "", t0, False, type(e).__name__)
            return await self._ollama_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)

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
        """Generate text from a prompt plus one or more images.

        Uses Claude vision-capable models when available. If Claude is unavailable,
        this raises instead of silently falling back because local Ollama is not
        configured for image understanding in this runtime.
        """
        if model == "auto":
            model = "smart"
        if model in ("local", "local-small"):
            raise RuntimeError("Local multimodal evaluation is not available")
        if not config.claude.api_key:
            raise RuntimeError("Claude vision unavailable: ANTHROPIC_API_KEY not configured")

        from shared.middleware import check_budget_for_llm_call
        model = await check_budget_for_llm_call(model)
        if model in ("local", "local-small"):
            raise RuntimeError("Claude vision downgraded to local model; multimodal evaluation unavailable")

        try:
            result = await self._claude_generate_with_images(
                prompt,
                images,
                system,
                model,
                max_tokens,
                temperature,
            )
            # Images add input cost too; overcount a little rather than undercount.
            image_token_overhead = len(images) * 2000
            await self._record_claude_spend(
                prompt,
                result,
                system,
                model,
                client_id=client_id,
                pipeline_stage=pipeline_stage,
                extra_input_tokens=image_token_overhead,
            )
            return result
        except Exception:
            raise

    async def _record_claude_spend(
        self,
        prompt: str,
        result: str,
        system: str,
        model: str,
        *,
        client_id: int | None = None,
        pipeline_stage: str = "",
        extra_input_tokens: float = 0.0,
    ):
        """Estimate and record the cost of a Claude API call, optionally tagged to a lead."""
        try:
            from shared.db import execute

            # Rough token estimate: ~4 chars per token
            input_tokens = ((len(prompt) + len(system)) / 4) + extra_input_tokens
            output_tokens = len(result) / 4
            total_tokens = input_tokens + output_tokens

            tier = "opus" if model == "genius" else ("haiku" if model == "fast" else "sonnet")
            cost = (total_tokens / 1000) * _COST_PER_1K[tier]

            # Only record if cost is meaningful (>$0.001)
            if cost >= 0.001:
                month = date.today().replace(day=1)
                desc = f"claude-{tier} ~{int(total_tokens)}tok"
                if pipeline_stage:
                    desc = f"{pipeline_stage}: {desc}"
                await execute(
                    """INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
                       VALUES (%s, 'claude_api', %s, %s, %s, %s)""",
                    (month, round(cost, 4), desc, client_id, pipeline_stage or None),
                )
        except Exception as e:
            logger.debug(f"Spend recording failed (non-critical): {e}")

    async def _claude_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float
    ) -> str:
        """Call Claude API via Anthropic messages endpoint."""
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = await _resolve_model(model)
        messages = [{"role": "user", "content": prompt}]

        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        resp = await self._get_http().post(
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

        if not data.get("content") or not data["content"]:
            raise RuntimeError("Empty response from Claude API")
        return data["content"][0]["text"]

    async def _claude_generate_with_images(
        self,
        prompt: str,
        images: list[bytes],
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Claude with image inputs."""
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = await _resolve_model(model)

        content: list[dict] = []
        for image in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(image).decode("utf-8"),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})

        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            body["system"] = system

        resp = await self._get_http().post(
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
        usage = data.get("usage", {})
        if usage:
            self._last_usage = usage
        return data["content"][0]["text"]

    async def _resolve_ollama_model(self, model: str, pipeline_stage: str = "") -> str:
        """Pick the best Ollama model: fine-tuned adapter if available, else base."""
        if pipeline_stage:
            try:
                from shared.db import get_config
                ft_model = await get_config("fine_tuned_model")
                if ft_model:
                    logger.debug("Using fine-tuned model %s for stage %s", ft_model, pipeline_stage)
                    return ft_model
            except Exception:
                pass  # Fall through to base model
        return await _resolve_model("local" if model == "local" else "local-small")

    async def _ollama_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float,
        pipeline_stage: str = "",
    ) -> str:
        """Call local Ollama API with TurboQuant KV cache compression when available."""
        model_name = await self._resolve_ollama_model(model, pipeline_stage)
        options: dict = {
            "num_predict": max_tokens,
            "temperature": temperature,
        }

        # TurboQuant KV cache compression — 4.9x memory reduction, ~zero accuracy loss
        # Requires Ollama >= 0.6.2 with llama.cpp TurboQuant support
        if config.ollama.kv_cache_type:
            options["cache_type_k"] = config.ollama.kv_cache_type
            options["cache_type_v"] = config.ollama.kv_cache_type
        if config.ollama.flash_attention:
            options["flash_attention"] = True

        body = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            body["system"] = system

        resp = await self._get_http().post(f"{config.ollama.host}/api/generate", json=body)
        if resp.status_code == 404 and model_name != config.ollama.secondary:
            # Model not found — fall back to secondary model
            logger.warning("Ollama model %s not found, falling back to %s", model_name, config.ollama.secondary)
            body["model"] = config.ollama.secondary
            resp = await self._get_http().post(f"{config.ollama.host}/api/generate", json=body)
        resp.raise_for_status()
        return resp.json()["response"]

    async def classify(self, text: str, categories: list[str]) -> str:
        """Quick classification using fast model."""
        if not categories:
            raise ValueError("categories list cannot be empty")
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
        resp = await self._get_http().post(
            f"{config.ollama.host}/api/embeddings",
            json={"model": config.ollama.embed_model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    async def close(self):
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()


# Singleton
llm = LLMClient()
