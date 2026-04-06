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

from __future__ import annotations

import asyncio
import base64
import json as _json
import logging
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import psycopg

from shared.airllm_policy import (
    choose_heavy_local_backend,
    explain_heavy_local_routing,
    should_route_to_heavy_local,
)
from shared.config import config
from shared.db import get_config
from shared.prompt_builder import CACHE_BOUNDARY_MARKER, get_session_latch

logger = logging.getLogger("perseus.llm")

# Sticky session month — computed once at import time so all budget queries within
# a process lifetime use the same month boundary, avoiding midnight drift.
_SESSION_MONTH = date.today().replace(day=1)

# Task 23-02: Feature flag for graduated model fallback chain
_UNIFIED_LLM_ENABLED = os.environ.get("ANATOMY_UNIFIED_LLM", "").lower() in ("true", "1")

# Task 26b-07: Feature flag for streaming/generator loop support
_GENERATOR_LOOP_ENABLED = os.environ.get("ANATOMY_GENERATOR_LOOP", "").lower() in ("true", "1")

# Task 26b-07: Watchdog timeout per model tier (seconds between chunks)
_STREAM_WATCHDOG_TIMEOUTS: dict[str, float] = {
    "fast": 30.0,    # Haiku
    "smart": 60.0,   # Sonnet
    "genius": 120.0,  # Opus
}
_STREAM_WATCHDOG_DEFAULT = 60.0


@dataclass
class StreamChunk:
    """A single chunk from a streaming LLM response."""

    text: str = ""
    chunk_type: str = "text_delta"  # text_delta | tool_use_start | tool_use_delta | message_stop
    tool_name: str = ""
    tool_id: str = ""
    tool_input_json: str = ""
    is_final: bool = False

# Task 23-02: Graduated model fallback chain.
# When a Claude API call fails, try the next tier before falling back to Ollama.
# "local" maps to Ollama — the terminal fallback.
_MODEL_FALLBACK_CHAIN: dict[str, list[str]] = {
    "genius": ["smart", "fast", "local"],
    "smart": ["fast", "local"],
    "fast": ["local"],
    "local": [],  # no fallback from local
}


async def check_budget_for_llm_call_import(model: str) -> str:
    """Lazy import wrapper to avoid circular imports in fallback chain."""
    from shared.middleware import check_budget_for_llm_call
    return await check_budget_for_llm_call(model)


async def _resolve_model(tier: str) -> str:
    """Resolve model ID, checking dashboard override first, then .env config.

    Phase 18b-04: Model tier lookups are latched via StickyLatch so that
    mid-session dashboard changes to model_genius / model_fast / etc. don't
    alter the system prompt and bust the Anthropic prompt cache.  The latch
    stores the first-seen value per db_key for the process lifetime.
    """
    _MODEL_MAP = {
        "genius": ("model_genius", config.claude.genius_model),
        "fast": ("model_fast", config.claude.fast_model),
        "smart": ("model_primary", config.claude.primary_model),
        "primary": ("model_primary", config.claude.primary_model),
        "local": ("model_local", config.ollama.model),
        "local-small": ("model_local_small", config.ollama.secondary),
        "local-heavy": ("model_local_heavy", config.airllm.model or config.ollama.model),
        "airllm": ("model_local_heavy", config.airllm.model or config.ollama.model),
        "embed": ("model_embed", config.ollama.embed_model),
    }
    db_key, fallback = _MODEL_MAP.get(tier, ("model_primary", config.claude.primary_model))

    # StickyLatch: latch the resolved model on first read per tier so cache
    # stays stable even if dashboard config changes mid-session.
    latch = get_session_latch()
    latch_key = f"model_tier:{db_key}"

    async def _fetch_model() -> str:
        override = await get_config(db_key, None)
        return str(override) if override else fallback

    # StickyLatch.get() is sync; for async factory we do a one-shot check:
    # if latched, return immediately; otherwise await the factory and latch.
    latched = latch.peek(latch_key)
    if latched is not None:
        return latched
    resolved = await _fetch_model()
    return latch.get(latch_key, lambda: resolved)

# Approximate cost per 1K tokens (input + output blended) as of March 2026
# Conservative estimates — better to overcount than undercount
_COST_PER_1K = {
    "haiku": 0.001,    # ~$0.25/M input + $1.25/M output blended
    "sonnet": 0.006,   # ~$3/M input + $15/M output blended
    "opus": 0.045,     # ~$15/M input + $75/M output blended
}


# Task 17-03: Optimal max_tokens per pipeline stage to reduce output token waste.
# Stages that only need a label get tiny budgets; generative stages get large ones.
_MAX_TOKENS_BY_STAGE: dict[str, int] = {
    "classify": 50,
    "extract": 256,
    "score": 128,
    "summarize": 512,
    "email_draft": 4096,
    "proposal": 8192,
    "site_build": 8192,
    "research": 4096,
    "brief": 2048,
}

# Default max_tokens used by generate() — used to detect "caller didn't override".
_DEFAULT_MAX_TOKENS = 2048


def _build_system_blocks(system_str: str) -> list[dict]:
    """Split a system prompt string into structured content blocks with cache_control.

    If the string contains CACHE_BOUNDARY_MARKER, the stable prefix (everything
    before the marker) gets ``cache_control: {"type": "ephemeral"}`` so Anthropic
    prompt caching can reuse it across calls.  The volatile suffix (marker +
    everything after) gets no cache_control.

    If the marker is NOT found, the entire string is cached as one block.

    Returns an empty list for empty/whitespace-only input.
    """
    if not system_str or not system_str.strip():
        return []

    idx = system_str.find(CACHE_BOUNDARY_MARKER)
    if idx >= 0:
        stable_prefix = system_str[:idx]
        volatile_suffix = system_str[idx:]  # includes the marker itself
        blocks: list[dict] = []
        if stable_prefix:
            blocks.append({
                "type": "text",
                "text": stable_prefix,
                "cache_control": {"type": "ephemeral"},
            })
        if volatile_suffix:
            blocks.append({
                "type": "text",
                "text": volatile_suffix,
            })
        return blocks

    # No boundary found — cache the entire string
    return [
        {
            "type": "text",
            "text": system_str,
            "cache_control": {"type": "ephemeral"},
        }
    ]


class _DeathSpiralGuard:
    """Circuit breaker that trips after repeated LLM failures to prevent runaway retries.

    When tripped, the guard enters a cooldown period during which all LLM calls are
    rejected with RuntimeError.  A single success resets the failure window.

    Thresholds are configurable via environment variables:
      LLM_SPIRAL_MAX_FAILURES  — failures needed to trip (default 5)
      LLM_SPIRAL_WINDOW        — sliding window in seconds (default 300)
      LLM_SPIRAL_COOLDOWN      — cooldown in seconds after tripping (default 60)
    """

    def __init__(self) -> None:
        self._max_failures = int(os.environ.get("LLM_SPIRAL_MAX_FAILURES", "5"))
        self._window = float(os.environ.get("LLM_SPIRAL_WINDOW", "300"))
        self._cooldown = float(os.environ.get("LLM_SPIRAL_COOLDOWN", "60"))
        self._failures: list[float] = []
        self._tripped_at: float | None = None

    def record_failure(self) -> None:
        """Record a failure timestamp; trip if threshold is exceeded."""
        now = time.monotonic()
        self._failures.append(now)
        # Prune failures outside the sliding window
        cutoff = now - self._window
        self._failures = [t for t in self._failures if t >= cutoff]
        if len(self._failures) >= self._max_failures:
            self._tripped_at = now
            logger.warning(
                "Death spiral guard tripped: %d failures in %.0fs window, cooldown %.0fs",
                len(self._failures),
                self._window,
                self._cooldown,
            )

    def record_success(self) -> None:
        """A successful call clears the failure history."""
        self._failures.clear()
        self._tripped_at = None

    @property
    def is_tripped(self) -> bool:
        """True if the guard is in cooldown after being tripped."""
        if self._tripped_at is None:
            return False
        elapsed = time.monotonic() - self._tripped_at
        if elapsed >= self._cooldown:
            # Cooldown expired — auto-reset
            self._tripped_at = None
            self._failures.clear()
            return False
        return True


class LLMClient:
    """Unified interface to Claude API + Ollama. Budget-aware.

    DEPRECATED: When ANATOMY_UNIFIED_LLM + UNIFIED_LLM_FACTORY are enabled,
    this class is bypassed in favor of shared.llm_factory.UnifiedLLMFactory.
    This class will be removed after the 48h parallel run validates the unified path.
    """

    def __init__(self):
        self._http: httpx.AsyncClient | None = None
        self._last_usage = None
        self._airllm_model: Any | None = None
        self._airllm_model_id: str | None = None
        self._airllm_lock: asyncio.Lock | None = None  # created lazily for Py 3.9 compat
        self._ollm_model: Any | None = None
        self._ollm_model_id: str | None = None
        self._ollm_lock: asyncio.Lock | None = None  # created lazily for Py 3.9 compat
        self._spiral_guard = _DeathSpiralGuard()

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
        cached_tokens: int = 0,
    ) -> None:
        """Fire-and-forget LLM metrics recording via asyncio.create_task.

        Phase 18b-01 Task 6: ``cached_tokens`` is populated from the API
        response's ``cache_read_input_tokens`` field when prompt caching is
        active.  It flows through to ``CostEvent.cached_tokens`` for spend
        attribution in the cost dashboard.
        """
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
                cached_tokens=cached_tokens,
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
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=90.0,
                    write=30.0,
                    pool=10.0,
                )
            )
        return self._http

    async def preconnect(self) -> None:
        """Fire a lightweight HEAD request to Claude API during startup.

        Overlaps TCP + TLS handshake with other init work (e.g. DB pool),
        saving 100-200ms on the first real API call.  Fire-and-forget:
        errors are silently ignored so startup is never blocked.

        Phase 28-14 (F-15).
        """
        try:
            client = self._get_http()
            await client.head(
                "https://api.anthropic.com/v1/messages",
                headers={"anthropic-version": "2023-06-01"},
            )
            logger.debug("API preconnect: TLS handshake completed")
        except (httpx.HTTPError, OSError, TimeoutError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            # Silently ignore — this is purely an optimization
            pass

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
        except (ImportError, OSError, AttributeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("DNA injection skipped: %s", exc)
            try:
                from shared.agent_dna import get_circuit_breaker

                get_circuit_breaker().record(False)
            except (ImportError, AttributeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.debug("Circuit breaker record failed: %s", exc)
            return system

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "auto",
        max_tokens: int = _DEFAULT_MAX_TOKENS,
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
        - "local-heavy"/"airllm": AirLLM heavy local path for research, memory digestion,
          and long-context offline reasoning when configured

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

        # Task 17-03: Output slot optimization — use stage-specific max_tokens when
        # the caller left the default value and a pipeline_stage is provided.
        if max_tokens == _DEFAULT_MAX_TOKENS and pipeline_stage:
            max_tokens = _MAX_TOKENS_BY_STAGE.get(pipeline_stage, _DEFAULT_MAX_TOKENS)

        # Task 17-04: Death spiral guard — reject calls while in cooldown
        if self._spiral_guard.is_tripped:
            raise RuntimeError(
                "LLM death spiral guard is active — too many consecutive failures. "
                "Calls are blocked for cooldown period."
            )

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

        # Task 27-03: Memory index injection — give every LLM call awareness of
        # available memories so it can reason about stored knowledge.
        if os.environ.get("ANATOMY_MEMORY_INDEX", "").lower() in ("true", "1"):
            from shared.memory_index import _MAX_ENTRIES, get_memory_index

            idx = get_memory_index()
            if idx:
                system = f"{system}\n\n## Memory Index ({_MAX_ENTRIES} entries)\n{idx}"

        t0 = time.perf_counter()
        resolved_model = model

        # "fast" and "smart" both use Claude (Haiku and Sonnet respectively)
        # Heavy local tiers go to AirLLM when available, then fall back to Ollama.
        if model in ("local-heavy", "airllm", "research-local", "ollm", "huge-context-local"):
            result = await self._heavy_local_or_ollama_generate(
                prompt,
                system,
                model,
                max_tokens,
                temperature,
                pipeline_stage,
            )
            self._fire_metrics(pipeline_stage or "unknown", "local-heavy", "generate", prompt, result, t0, True, None)
            return result

        # Only local tiers skip the cloud path entirely.
        if model in ("local", "local-small"):
            result = await self._best_local_generate(prompt, system, model, max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", model, "generate", prompt, result, t0, True, None)
            return result

        # If no API key, fall back to Ollama for everything
        if not config.claude.api_key:
            result = await self._best_local_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", "local", "generate", prompt, result, t0, True, None)
            return result

        # Budget check — consolidated middleware authority
        from shared.middleware import check_budget_for_llm_call
        resolved_model = await check_budget_for_llm_call(model)

        if resolved_model in ("local", "local-small"):
            # Budget gate downgraded us
            result = await self._best_local_generate(prompt, system, resolved_model, max_tokens, temperature, pipeline_stage)
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, result, t0, True, None)
            return result

        # Task 23-02: Graduated fallback chain when ANATOMY_UNIFIED_LLM is enabled
        if _UNIFIED_LLM_ENABLED:
            return await self._generate_with_fallback_chain(
                prompt, system, resolved_model, max_tokens, temperature,
                client_id, pipeline_stage, t0,
            )

        # Legacy binary fallback: Claude fails → Ollama
        try:
            result = await self._claude_generate(prompt, system, resolved_model, max_tokens, temperature)
            self._spiral_guard.record_success()
            await self._record_claude_spend(
                prompt,
                result,
                system,
                resolved_model,
                client_id=client_id,
                pipeline_stage=pipeline_stage,
                usage=self._last_usage,
            )
            # Phase 18b: Pass cached token count from API response to cost event
            _cached = 0
            if self._last_usage:
                _cached = self._last_usage.get("cache_read_input_tokens", 0)
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, result, t0, True, None, cached_tokens=_cached)
            # Task 27-06: Background learning extraction from LLM responses
            from shared.learning_extractor import maybe_extract_background
            maybe_extract_background(result, pipeline_stage or "")
            return result
        except Exception as e:  # Intentional: model fallback catch-all (Claude → Ollama)  # IGUS-FIX
            self._spiral_guard.record_failure()
            logger.warning(f"Claude API failed, falling back to Ollama: {e}")
            self._fire_metrics(pipeline_stage or "unknown", resolved_model, "generate", prompt, "", t0, False, type(e).__name__)
            return await self._best_local_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)

    @staticmethod
    def _strip_thinking_blocks(text: str) -> str:
        """Strip extended thinking blocks that Opus may return.

        When falling back from Opus to Sonnet/Haiku, the response may contain
        <thinking>...</thinking> blocks that downstream consumers don't expect.
        """
        import re
        return re.sub(r"<thinking>.*?</thinking>\s*", "", text, flags=re.DOTALL).strip()

    async def _generate_with_fallback_chain(
        self,
        prompt: str,
        system: str,
        starting_model: str,
        max_tokens: int,
        temperature: float,
        client_id: int | None,
        pipeline_stage: str,
        t0: float,
    ) -> str:
        """Graduated fallback chain: Opus -> Sonnet -> Haiku -> Ollama.

        Each tier is tried in order. Failures are logged with model, error,
        and attempt number. Thinking blocks are stripped when falling back
        from genius to lower tiers.
        """
        chain = [starting_model] + _MODEL_FALLBACK_CHAIN.get(starting_model, ["local"])
        last_error: Exception | None = None

        for attempt, model_tier in enumerate(chain, start=1):
            if model_tier == "local":
                # Terminal fallback — use Ollama
                logger.info(
                    "Fallback chain: attempt %d/%d, falling back to Ollama (local) "
                    "after %s failure: %s",
                    attempt,
                    len(chain),
                    starting_model,
                    last_error,
                )
                return await self._best_local_generate(
                    prompt, system, "local", max_tokens, temperature, pipeline_stage
                )

            try:
                resolved = await check_budget_for_llm_call_import(model_tier)
                if resolved in ("local", "local-small"):
                    # Budget gate downgraded us — skip to next tier
                    logger.info(
                        "Fallback chain: attempt %d/%d, budget downgraded %s to %s, skipping",
                        attempt, len(chain), model_tier, resolved,
                    )
                    last_error = RuntimeError(f"Budget downgraded {model_tier} to {resolved}")
                    continue

                result = await self._claude_generate(prompt, system, resolved, max_tokens, temperature)
                self._spiral_guard.record_success()
                await self._record_claude_spend(
                    prompt, result, system, resolved,
                    client_id=client_id,
                    pipeline_stage=pipeline_stage,
                    usage=self._last_usage,
                )
                # Phase 18b: pass cached token count to cost event
                _fc_cached = self._last_usage.get("cache_read_input_tokens", 0) if self._last_usage else 0
                self._fire_metrics(
                    pipeline_stage or "unknown", resolved, "generate",
                    prompt, result, t0, True, None,
                    cached_tokens=_fc_cached,
                )

                # Strip thinking blocks when we fell back from genius
                if model_tier != starting_model and starting_model == "genius":
                    result = self._strip_thinking_blocks(result)

                if attempt > 1:
                    logger.info(
                        "Fallback chain: succeeded on attempt %d/%d with model %s "
                        "(original: %s)",
                        attempt, len(chain), model_tier, starting_model,
                    )
                return result

            except Exception as e:  # Intentional: model fallback catch-all (graduated chain)  # IGUS-FIX
                self._spiral_guard.record_failure()
                last_error = e
                logger.warning(
                    "Fallback chain: attempt %d/%d, model %s failed: %s. "
                    "Trying next tier.",
                    attempt, len(chain), model_tier, e,
                )
                self._fire_metrics(
                    pipeline_stage or "unknown", model_tier, "generate",
                    prompt, "", t0, False, type(e).__name__,
                )
                continue

        # Should not reach here, but safety net
        logger.error("Fallback chain exhausted for model %s", starting_model)
        return await self._best_local_generate(
            prompt, system, "local", max_tokens, temperature, pipeline_stage
        )

    async def generate_stream(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "auto",
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        pipeline_stage: str = "",
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream LLM response chunks via SSE, with a watchdog timer per model tier.

        When ANATOMY_GENERATOR_LOOP is disabled, falls back to a single-chunk
        yield of the non-streaming generate() result.

        The watchdog timer monitors time between chunks.  If no chunk arrives
        within the tier-specific timeout, streaming is abandoned and the method
        falls back to a non-streaming generate() call, yielding the full result
        as a single chunk.

        Parses Anthropic SSE event types:
        - message_start, content_block_start, content_block_delta,
          content_block_stop, message_delta, message_stop

        Tool-use blocks are detected at content_block_start and emitted as
        StreamChunk(chunk_type="tool_use_start") so callers can begin tool
        execution before the full response completes.

        Phase 26b-07 (Anatomy Integration B-06).
        """
        if not _GENERATOR_LOOP_ENABLED:
            # Feature flag off: delegate to non-streaming generate()
            result = await self.generate(
                prompt, system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                pipeline_stage=pipeline_stage,
            )
            yield StreamChunk(text=result, chunk_type="text_delta", is_final=True)
            return

        if model == "auto":
            model = "fast"

        # Check prerequisites for streaming
        if not config.claude.api_key or model in ("local", "local-small", "local-heavy", "airllm"):
            result = await self.generate(
                prompt, system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                pipeline_stage=pipeline_stage,
            )
            yield StreamChunk(text=result, chunk_type="text_delta", is_final=True)
            return

        watchdog_timeout = _STREAM_WATCHDOG_TIMEOUTS.get(model, _STREAM_WATCHDOG_DEFAULT)

        try:
            async for chunk in self._claude_generate_stream(
                prompt, system, model, max_tokens, temperature, watchdog_timeout
            ):
                yield chunk
        except TimeoutError:
            logger.warning(
                "Stream watchdog fired for model=%s (timeout=%.0fs), "
                "falling back to non-streaming call",
                model, watchdog_timeout,
            )
            result = await self.generate(
                prompt, system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                pipeline_stage=pipeline_stage,
            )
            yield StreamChunk(text=result, chunk_type="text_delta", is_final=True)
        except Exception as e:  # Intentional: model fallback catch-all (stream → non-stream)  # IGUS-FIX
            logger.warning(
                "Streaming failed for model=%s: %s, falling back to non-streaming",
                model, e,
            )
            result = await self.generate(
                prompt, system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                pipeline_stage=pipeline_stage,
            )
            yield StreamChunk(text=result, chunk_type="text_delta", is_final=True)

    async def _claude_generate_stream(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        watchdog_timeout: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Internal SSE streaming against the Anthropic Messages API.

        Raises asyncio.TimeoutError if no chunk arrives within watchdog_timeout.
        """
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = await _resolve_model(model)
        messages = [{"role": "user", "content": prompt}]

        prompt_cache_enabled = os.environ.get(
            "ANATOMY_PROMPT_CACHE", ""
        ).lower() in ("true", "1")

        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
        }
        if system:
            if prompt_cache_enabled:
                body["system"] = _build_system_blocks(system)
            else:
                body["system"] = system

        headers = {
            "x-api-key": config.claude.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if prompt_cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        client = self._get_http()

        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers=headers,
            timeout=max(watchdog_timeout * 2, 120.0),
        ) as response:
            response.raise_for_status()

            current_tool_name = ""
            current_tool_id = ""
            tool_input_parts: list[str] = []

            async for raw_line in self._sse_lines_with_watchdog(
                response, watchdog_timeout
            ):
                # Parse SSE format: "event: <type>\ndata: <json>"
                if not raw_line.startswith("data: "):
                    continue
                json_str = raw_line[6:]
                if json_str.strip() == "[DONE]":
                    break

                try:
                    event = _json.loads(json_str)
                except _json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool_name = block.get("name", "")
                        current_tool_id = block.get("id", "")
                        tool_input_parts = []
                        yield StreamChunk(
                            chunk_type="tool_use_start",
                            tool_name=current_tool_name,
                            tool_id=current_tool_id,
                        )

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        yield StreamChunk(
                            text=delta.get("text", ""),
                            chunk_type="text_delta",
                        )
                    elif delta_type == "input_json_delta":
                        partial = delta.get("partial_json", "")
                        tool_input_parts.append(partial)
                        yield StreamChunk(
                            chunk_type="tool_use_delta",
                            tool_name=current_tool_name,
                            tool_id=current_tool_id,
                            tool_input_json=partial,
                        )

                elif event_type == "content_block_stop":
                    if current_tool_name:
                        current_tool_name = ""
                        current_tool_id = ""
                        tool_input_parts = []

                elif event_type == "message_stop":
                    yield StreamChunk(chunk_type="message_stop", is_final=True)
                    return

                elif event_type == "message_delta":
                    # Contains stop_reason, usage, etc.
                    pass

        # If we exit the stream without message_stop, yield final
        yield StreamChunk(chunk_type="message_stop", is_final=True)

    async def _sse_lines_with_watchdog(
        self,
        response: httpx.Response,
        watchdog_timeout: float,
    ) -> AsyncGenerator[str, None]:
        """Iterate SSE lines from an httpx streaming response with a watchdog timer.

        Raises asyncio.TimeoutError if no line arrives within watchdog_timeout seconds.
        """
        buffer = ""
        async for raw_bytes in self._aiter_bytes_with_watchdog(response, watchdog_timeout):
            buffer += raw_bytes.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                stripped = line.strip()
                if stripped:
                    yield stripped

    async def _aiter_bytes_with_watchdog(
        self,
        response: httpx.Response,
        watchdog_timeout: float,
    ) -> AsyncGenerator[bytes, None]:
        """Iterate bytes from httpx response, raising TimeoutError on watchdog expiry."""
        aiter = response.aiter_bytes().__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(), timeout=watchdog_timeout
                )
                yield chunk
            except StopAsyncIteration:
                return

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
                usage=self._last_usage,
            )
            return result
        except Exception:  # Intentional: bare re-raise preserves caller error handling  # IGUS-FIX
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
        usage: dict | None = None,
    ):
        """Estimate and record the cost of a Claude API call, optionally tagged to a lead.

        When ``usage`` is provided and the ``ANATOMY_ACCURATE_TOKENS`` env var is
        truthy, real token counts from the API response are used instead of the
        ``len(text)//4`` heuristic.  Cached tokens (prompt caching) are subtracted
        from the billable input count.
        """
        try:
            from shared.db import execute

            use_accurate = (
                usage is not None
                and os.environ.get("ANATOMY_ACCURATE_TOKENS", "").lower() in ("true", "1")
            )

            if use_accurate:
                cached = usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                input_tokens = max(0, usage.get("input_tokens", 0) - cached) + extra_input_tokens
                output_tokens = usage.get("output_tokens", 0)
            else:
                # Rough token estimate: ~4 chars per token
                input_tokens = ((len(prompt) + len(system)) / 4) + extra_input_tokens
                output_tokens = len(result) / 4
            total_tokens = input_tokens + output_tokens

            tier = "opus" if model == "genius" else ("haiku" if model == "fast" else "sonnet")
            cost = (total_tokens / 1000) * _COST_PER_1K[tier]

            # Only record if cost is meaningful (>$0.001)
            if cost >= 0.001:
                month = _SESSION_MONTH
                desc = f"claude-{tier} ~{int(total_tokens)}tok"
                if pipeline_stage:
                    desc = f"{pipeline_stage}: {desc}"
                await execute(
                    """INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
                       VALUES (%s, 'claude_api', %s, %s, %s, %s)""",
                    (month, round(cost, 4), desc, client_id, pipeline_stage or None),
                )
        except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            # IGUS-FIX: Budget guard fail-closed — DB error blocks Claude, falls back to local (AEGIS 1.3)
            # If we can't record spend, budget tracking becomes inaccurate and future
            # calls will see $0 spend, effectively failing open. Raise so caller
            # falls back to Ollama rather than allowing untracked Claude spend.
            logger.critical("Spend recording DB failed — failing closed to prevent untracked spend: %s", e)
            raise RuntimeError(f"Budget guard fail-closed: spend recording failed ({e})") from e

    async def _claude_generate(
        self, prompt: str, system: str, model: str, max_tokens: int, temperature: float
    ) -> str:
        """Call Claude API via Anthropic messages endpoint."""
        if not config.claude.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_id = await _resolve_model(model)
        messages = [{"role": "user", "content": prompt}]

        prompt_cache_enabled = os.environ.get(
            "ANATOMY_PROMPT_CACHE", ""
        ).lower() in ("true", "1")

        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            if prompt_cache_enabled:
                body["system"] = _build_system_blocks(system)
            else:
                body["system"] = system

        # Model-specific request timeout: genius (Opus) gets longer for complex reasoning
        _MODEL_TIMEOUTS = {"genius": 180.0, "fast": 60.0}
        request_timeout = _MODEL_TIMEOUTS.get(model, 120.0)

        headers = {
            "x-api-key": config.claude.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if prompt_cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        resp = await self._get_http().post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers=headers,
            timeout=request_timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # Use actual token counts from API response if available
        usage = data.get("usage", {})
        if usage:
            self._last_usage = usage  # Cache for more accurate spend recording

        # Log cache effectiveness when prompt caching is active
        if prompt_cache_enabled and usage:
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            if cache_read or cache_creation:
                logger.info(
                    "Prompt cache stats: read=%d creation=%d tokens",
                    cache_read,
                    cache_creation,
                )

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
        prompt_cache_enabled = os.environ.get(
            "ANATOMY_PROMPT_CACHE", ""
        ).lower() in ("true", "1")

        if system:
            if prompt_cache_enabled:
                body["system"] = _build_system_blocks(system)
            else:
                body["system"] = system

        headers = {
            "x-api-key": config.claude.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if prompt_cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        resp = await self._get_http().post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers=headers,
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
            except (ImportError, psycopg.Error, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # Fall through to base model
        return await _resolve_model("local" if model == "local" else "local-small")

    async def _best_local_generate(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str = "",
    ) -> str:
        """Route local-heavy work to AirLLM when configured, otherwise Ollama."""
        if should_route_to_heavy_local(
            requested_model=model,
            pipeline_stage=pipeline_stage,
            prompt=prompt,
            system=system,
        ):
            return await self._heavy_local_or_ollama_generate(
                prompt,
                system,
                model,
                max_tokens,
                temperature,
                pipeline_stage,
            )
        return await self._ollama_generate(prompt, system, model, max_tokens, temperature, pipeline_stage)

    async def _heavy_local_or_ollama_generate(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str = "",
    ) -> str:
        """Try the best heavy-local backend first, then degrade gracefully."""
        selected_backend = choose_heavy_local_backend(
            requested_model=model,
            pipeline_stage=pipeline_stage,
            prompt=prompt,
            system=system,
        )
        try:
            if selected_backend == "ollm":
                return await self._ollm_generate(prompt, system, model, max_tokens, temperature, pipeline_stage)
            if selected_backend == "airllm":
                return await self._airllm_generate(prompt, system, model, max_tokens, temperature, pipeline_stage)
            raise RuntimeError("No heavy-local backend selected")
        except Exception as exc:  # Intentional: model fallback catch-all (heavy-local → Ollama)  # IGUS-FIX
            logger.warning(
                "Heavy local backend unavailable, falling back to Ollama (%s via %s): %s",
                pipeline_stage or "no-stage",
                explain_heavy_local_routing(
                    requested_model=model,
                    pipeline_stage=pipeline_stage,
                    prompt=prompt,
                    system=system,
                ),
                exc,
            )
            return await self._ollama_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)

    async def _airllm_generate(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str = "",
    ) -> str:
        """Best-effort AirLLM path for heavy offline local reasoning.

        AirLLM is intentionally treated as a sidecar-grade heavy local backend:
        great for batch research and long-context analysis, not for low-latency
        Kirito voice turns.
        """
        if not config.airllm.enabled:
            raise RuntimeError("AIRLLM_ENABLED is false")

        model_name = await _resolve_model(model)
        model_obj = await self._get_airllm_model(model_name)
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"

        return await asyncio.to_thread(
            self._airllm_generate_sync,
            model_obj,
            full_prompt,
            max_tokens,
            temperature,
            pipeline_stage,
        )

    async def _get_airllm_model(self, model_name: str) -> Any:
        if self._airllm_lock is None:
            self._airllm_lock = asyncio.Lock()
        async with self._airllm_lock:
            if self._airllm_model is not None and self._airllm_model_id == model_name:
                return self._airllm_model

            try:
                from airllm import AutoModel
            except (ImportError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed from bare Exception
                raise RuntimeError("airllm package is not installed") from exc

            kwargs: dict[str, Any] = {
                "compression": config.airllm.compression,
                "profiling_mode": config.airllm.profiling_mode,
            }
            if config.airllm.layer_shards_path:
                kwargs["layer_shards_path"] = config.airllm.layer_shards_path
            if config.airllm.hf_token:
                kwargs["hf_token"] = config.airllm.hf_token

            model_obj = await asyncio.to_thread(AutoModel.from_pretrained, model_name, **kwargs)
            self._airllm_model = model_obj
            self._airllm_model_id = model_name
            return model_obj

    async def _ollm_generate(
        self,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str = "",
    ) -> str:
        """Best-effort oLLM path for ultra-large local contexts."""
        if not config.ollm.enabled:
            raise RuntimeError("OLLM_ENABLED is false")

        model_name = config.ollm.model or await _resolve_model(model)
        model_obj = await self._get_ollm_model(model_name)
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"
        return await asyncio.to_thread(
            self._ollm_generate_sync,
            model_obj,
            full_prompt,
            max_tokens,
            temperature,
            pipeline_stage,
        )

    async def _get_ollm_model(self, model_name: str) -> Any:
        if self._ollm_lock is None:
            self._ollm_lock = asyncio.Lock()
        async with self._ollm_lock:
            if self._ollm_model is not None and self._ollm_model_id == model_name:
                return self._ollm_model

            try:
                from ollm import AutoInference
            except (ImportError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed from bare Exception
                raise RuntimeError("ollm package is not installed") from exc

            # oLLM is best for very large offline contexts; default to CPU/MPS friendly mode.
            kwargs: dict[str, Any] = {"logging": True}
            if os.environ.get("CUDA_VISIBLE_DEVICES", ""):
                kwargs["device"] = "cuda:0"
            elif os.uname().sysname == "Darwin":
                kwargs["device"] = "mps"
            else:
                kwargs["device"] = "cpu"

            model_obj = await asyncio.to_thread(AutoInference, model_name, **kwargs)
            init_method = getattr(model_obj, "ini_model", None)
            if callable(init_method):
                await asyncio.to_thread(init_method)
            self._ollm_model = model_obj
            self._ollm_model_id = model_name
            return model_obj

    def _ollm_generate_sync(
        self,
        model_obj: Any,
        full_prompt: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str,
    ) -> str:
        tokenizer = getattr(model_obj, "tokenizer", None)
        model = getattr(model_obj, "model", None)
        device = getattr(model_obj, "device", None)
        if tokenizer is None or model is None or device is None:
            raise RuntimeError(f"oLLM model is missing tokenizer/model/device for stage {pipeline_stage or 'unknown'}")

        try:
            import torch
        except (ImportError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed from bare Exception
            raise RuntimeError("torch is required for ollm generation") from exc

        messages = [
            {"role": "user", "content": full_prompt},
        ]
        input_ids = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(device)
        outputs = model.generate(
            input_ids=input_ids,
            max_new_tokens=max_tokens,
            temperature=temperature,
        )
        return tokenizer.decode(outputs[0][input_ids.shape[-1]:], skip_special_tokens=False)

    def _airllm_generate_sync(
        self,
        model_obj: Any,
        full_prompt: str,
        max_tokens: int,
        temperature: float,
        pipeline_stage: str,
    ) -> str:
        try:
            import torch
        except ImportError:  # IGUS-FIX: Narrowed from bare Exception — torch is optional
            torch = None

        tokenizer = getattr(model_obj, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("AirLLM model has no tokenizer")

        input_tokens = tokenizer(
            [full_prompt],
            return_tensors="pt",
            return_attention_mask=False,
            truncation=True,
            max_length=4096,
            padding=False,
        )

        input_ids = input_tokens["input_ids"]
        if torch is not None and hasattr(torch, "cuda") and torch.cuda.is_available():
            input_ids = input_ids.cuda()
        elif torch is not None and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            input_ids = input_ids.to("mps")

        generation_output = model_obj.generate(
            input_ids,
            max_new_tokens=max_tokens,
            use_cache=True,
            return_dict_in_generate=True,
            temperature=temperature,
        )
        sequences = getattr(generation_output, "sequences", None)
        if sequences is None:
            raise RuntimeError(f"AirLLM generation returned no sequences for stage {pipeline_stage or 'unknown'}")
        return tokenizer.decode(sequences[0], skip_special_tokens=True)

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


# ---------------------------------------------------------------------------
# Task 23-01: Unified LLM Protocol & Factory (Anatomy Integration Phase 23)
#
# Two LLM paths exist: LLMClient (daemon path with budget/DNA/cost recording)
# and CloudEngine (OpenJarvis engine path with multi-provider support).
# The protocol defines the common interface; the factory chooses the backend.
#
# When ANATOMY_UNIFIED_LLM=true, the factory wraps CloudEngine with the
# cross-cutting concerns (budget, DNA, cost recording, prompt caching) that
# LLMClient provides.  When false (default), the existing LLMClient is used
# unchanged — zero behavior change.
# ---------------------------------------------------------------------------

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProtocol(Protocol):
    """Common interface that both LLM backends must satisfy.

    This protocol allows callers to depend on a stable interface regardless
    of whether the underlying backend is LLMClient or a CloudEngine wrapper.
    """

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "smart",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str: ...

    async def generate_with_images(
        self,
        prompt: str,
        *,
        images: list[bytes],
        system: str = "",
        model: str = "smart",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str: ...


class _CloudEngineWrapper:
    """Wraps openjarvis.engine.cloud.CloudEngine behind LLMProtocol.

    Adds the cross-cutting concerns from LLMClient:
    - Budget enforcement via shared.middleware
    - DNA injection via shared.agent_dna
    - Cost recording via shared.db
    - Death spiral guard
    - Prompt caching support

    The CloudEngine is lazily imported so that this module doesn't pull in
    heavy SDK dependencies at import time.
    """

    def __init__(self) -> None:
        self._engine: Any = None
        self._spiral_guard = _DeathSpiralGuard()

    def _get_engine(self) -> Any:
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
        model: str = "smart",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        client_id: int | None = None,
        pipeline_stage: str = "",
        use_dna: bool = False,
        daemon_name: str = "",
        **kwargs: Any,
    ) -> str:
        """Generate text via CloudEngine with LLMClient cross-cutting concerns."""
        if model == "auto":
            model = "fast"

        # Death spiral guard
        if self._spiral_guard.is_tripped:
            raise RuntimeError(
                "LLM death spiral guard is active — too many consecutive failures. "
                "Calls are blocked for cooldown period."
            )

        # DNA injection
        if use_dna and daemon_name:
            system = LLMClient._inject_dna(system, daemon_name)

        # Resolve model tier to actual model ID
        resolved_model = await _resolve_model(model)

        # Budget check
        if config.claude.api_key and model not in ("local", "local-small", "local-heavy", "airllm"):
            from shared.middleware import check_budget_for_llm_call
            budget_result = await check_budget_for_llm_call(model)
            if budget_result in ("local", "local-small"):
                # Budget downgraded — fall back to LLMClient's local path
                # since CloudEngine doesn't support Ollama.
                _fallback = LLMClient()
                return await _fallback._best_local_generate(
                    prompt, system, budget_result, max_tokens, temperature, pipeline_stage
                )
            resolved_model = await _resolve_model(budget_result)

        # Build messages for CloudEngine (it expects Message objects or dicts)
        from openjarvis.core.types import Message, MessageRole
        messages: list[Message] = []
        if system:
            messages.append(Message(role=MessageRole.SYSTEM, content=system))
        messages.append(Message(role=MessageRole.USER, content=prompt))

        try:
            engine = self._get_engine()
            result_dict = engine.generate(
                messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._spiral_guard.record_success()
            content = result_dict.get("content", "")

            # Record cost to DB (fire-and-forget)
            usage = result_dict.get("usage", {})
            cost_usd = result_dict.get("cost_usd", 0.0)
            try:
                from shared.db import execute
                loop = asyncio.get_running_loop()
                loop.create_task(
                    execute(
                        "INSERT INTO llm_spend (month, model, prompt_tokens, "
                        "completion_tokens, cost_usd, pipeline_stage) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            _SESSION_MONTH,
                            resolved_model,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                            cost_usd,
                            pipeline_stage or "unknown",
                        ),
                    )
                )
            except (psycopg.Error, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # Cost recording is best-effort

            return content

        except Exception as e:  # Intentional: model fallback catch-all (CloudEngine → Ollama)  # IGUS-FIX
            self._spiral_guard.record_failure()
            logger.warning("CloudEngine generate failed: %s, falling back to LLMClient Ollama", e)
            _fallback = LLMClient()
            return await _fallback._best_local_generate(
                prompt, system, "local", max_tokens, temperature, pipeline_stage
            )

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
        **kwargs: Any,
    ) -> str:
        """Generate text from prompt + images via CloudEngine.

        CloudEngine doesn't natively support image inputs in all providers,
        so this delegates to LLMClient's image path which uses the Anthropic
        SDK directly.  This preserves the budget and cost-recording behavior.
        """
        _fallback = LLMClient()
        return await _fallback.generate_with_images(
            prompt,
            images=images,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            client_id=client_id,
            pipeline_stage=pipeline_stage,
        )

    async def close(self) -> None:
        """Release CloudEngine resources."""
        if self._engine is not None:
            self._engine.close()
            self._engine = None


class UnifiedLLMFactory:
    """Factory that creates the appropriate LLM backend based on configuration.

    Decision tree (when ANATOMY_UNIFIED_LLM=true):
    1. If USE_OJ_ENGINE=true -> _CloudEngineWrapper (CloudEngine + cross-cutting)
    2. If UNIFIED_LLM_FACTORY=true -> shared.llm_factory.UnifiedLLMFactory (Phase 23 provider-based)
    3. Otherwise -> LLMClient (existing behavior with graduated fallback chain)
    """

    @staticmethod
    def create() -> Any:
        """Create the appropriate LLM backend."""
        use_oj = os.environ.get("USE_OJ_ENGINE", "").lower() in ("true", "1")
        use_factory = os.environ.get("UNIFIED_LLM_FACTORY", "").lower() in ("true", "1")

        if use_oj:
            logger.info("UnifiedLLMFactory: creating CloudEngine wrapper (USE_OJ_ENGINE=true)")
            return _CloudEngineWrapper()
        if use_factory:
            logger.info("UnifiedLLMFactory: creating provider-based factory (UNIFIED_LLM_FACTORY=true)")
            from shared.llm_factory import UnifiedLLMFactory as ProviderFactory
            return ProviderFactory()
        logger.info("UnifiedLLMFactory: creating standard LLMClient")
        return LLMClient()


def _create_llm_client() -> Any:
    """Create the LLM singleton, respecting the ANATOMY_UNIFIED_LLM feature flag.

    When the flag is OFF (default), the existing LLMClient is used unchanged.
    When ON, the UnifiedLLMFactory decides which backend to instantiate:
      - USE_OJ_ENGINE=true -> CloudEngine wrapper
      - UNIFIED_LLM_FACTORY=true -> Phase 23 provider-based factory
      - Otherwise -> LLMClient with graduated fallback chain
    """
    if os.environ.get("ANATOMY_UNIFIED_LLM", "").lower() in ("true", "1"):
        return UnifiedLLMFactory.create()
    return LLMClient()


# Singleton -- may be LLMClient, _CloudEngineWrapper, or UnifiedLLMFactory
# depending on feature flags
llm = _create_llm_client()
