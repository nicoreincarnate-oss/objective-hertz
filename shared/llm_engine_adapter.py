"""
LLM Engine Adapter -- Drop-in replacement for shared.llm_client that can route
through OpenJarvis InferenceEngine when the USE_OJ_ENGINE feature flag is enabled.

DEPRECATED: Use shared.llm_client (with ANATOMY_UNIFIED_LLM=true and
UNIFIED_LLM_FACTORY=true) instead. This module will be removed after the
Phase 23 48h parallel run validates the unified path.

Feature flag: USE_OJ_ENGINE env var (default "false")
  - When false: delegates 100% to the original LLMClient (zero behavior change)
  - When true: routes through OJ Engine with budget-aware tier resolution

This module exports the same interface as shared.llm_client:
  - LLMClient class (with identical generate/generate_with_images/classify/embed/close)
  - llm singleton instance

Import guard: if OJ engine imports fail (wrong Python version, missing deps),
falls back to original llm_client silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date
from typing import Any

# Sticky session month — computed once at import time to avoid midnight drift.
_SESSION_MONTH = date.today().replace(day=1)

logger = logging.getLogger("perseus.llm.adapter")

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

_USE_OJ_ENGINE = os.environ.get("USE_OJ_ENGINE", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Try importing OJ Engine components — fail silently to original client
# ---------------------------------------------------------------------------

_OJ_AVAILABLE = False
_oj_multi_engine: Any = None
_oj_cloud_engine: Any = None
_oj_ollama_engine: Any = None
_OJ_Message: Any = None
_OJ_Role: Any = None
_oj_estimate_cost: Any = None

if _USE_OJ_ENGINE:
    try:
        from openjarvis.core.types import Message as _Msg, Role as _R
        from openjarvis.engine.cloud import CloudEngine, estimate_cost as _est_cost
        from openjarvis.engine.multi import MultiEngine
        from openjarvis.engine.ollama import OllamaEngine

        _OJ_Message = _Msg
        _OJ_Role = _R
        _oj_estimate_cost = _est_cost
        _OJ_AVAILABLE = True
        logger.info("OJ Engine imports succeeded — adapter will route through OJ when enabled")
    except (ImportError, ModuleNotFoundError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("OJ Engine imports failed, falling back to original llm_client: %s", exc)
        _OJ_AVAILABLE = False

# ---------------------------------------------------------------------------
# Try importing model_selector — fail silently to hardcoded tier map
# ---------------------------------------------------------------------------

_MODEL_SELECTOR_AVAILABLE = False
_select_model_fn: Any = None

_select_model_with_t2_fn: Any = None
_T2Selection: Any = None
_execute_with_t2_fn: Any = None

try:
    from shared.model_selector import T2Selection as _T2Sel
    from shared.model_selector import execute_with_t2 as _exec_t2
    from shared.model_selector import select_model as _sel_model
    from shared.model_selector import select_model_with_t2 as _sel_t2
    _select_model_fn = _sel_model
    _select_model_with_t2_fn = _sel_t2
    _T2Selection = _T2Sel
    _execute_with_t2_fn = _exec_t2
    _MODEL_SELECTOR_AVAILABLE = True
    logger.debug("Model selector available — dynamic tier resolution enabled")
except (ImportError, ModuleNotFoundError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
    logger.debug("Model selector unavailable, using hardcoded tier map: %s", exc)
    _MODEL_SELECTOR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Tier -> model resolution for OJ Engine
# ---------------------------------------------------------------------------

_OJ_TIER_MAP: dict[str, tuple[str, str]] = {
    # tier -> (model_id, engine_key)
    "fast": ("claude-3-5-haiku-latest", "cloud"),
    "auto": ("claude-3-5-haiku-latest", "cloud"),
    "smart": ("claude-sonnet-4-20250514", "cloud"),
    "primary": ("claude-sonnet-4-20250514", "cloud"),
    "genius": ("claude-opus-4-20250514", "cloud"),
    "local": ("qwen2.5:14b-instruct-q4_K_M", "ollama"),
    "local-small": ("llama3.2:3b", "ollama"),
}

# Cost-per-1K tokens for char-count estimation fallback (matches llm_client.py)
_COST_PER_1K = {
    "haiku": 0.001,
    "sonnet": 0.006,
    "opus": 0.045,
}

# ---------------------------------------------------------------------------
# OJ Engine singleton management
# ---------------------------------------------------------------------------

_oj_engine_lock = asyncio.Lock() if _OJ_AVAILABLE else None
_oj_engines_initialized = False


def _init_oj_engines() -> Any:
    """Lazily create the MultiEngine wrapping cloud + ollama backends."""
    global _oj_multi_engine, _oj_cloud_engine, _oj_ollama_engine, _oj_engines_initialized

    if _oj_engines_initialized and _oj_multi_engine is not None:
        return _oj_multi_engine

    try:
        cloud_eng = CloudEngine()
        ollama_eng = OllamaEngine()
        multi = MultiEngine([("cloud", cloud_eng), ("ollama", ollama_eng)])

        _oj_cloud_engine = cloud_eng
        _oj_ollama_engine = ollama_eng
        _oj_multi_engine = multi
        _oj_engines_initialized = True
        return multi
    except (ImportError, OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Failed to initialize OJ engines: %s", exc)
        raise


def _make_messages(prompt: str, system: str = "") -> list:
    """Build OJ Message objects from prompt + system string."""
    msgs = []
    if system:
        msgs.append(_OJ_Message(role=_OJ_Role.SYSTEM, content=system))
    msgs.append(_OJ_Message(role=_OJ_Role.USER, content=prompt))
    return msgs


def _tier_to_cost_key(tier: str) -> str:
    """Map Perseus tier to cost lookup key."""
    if tier in ("fast", "auto"):
        return "haiku"
    if tier == "genius":
        return "opus"
    return "sonnet"


# ---------------------------------------------------------------------------
# Budget checking (reuses existing middleware)
# ---------------------------------------------------------------------------


async def _check_budget(tier: str) -> str:
    """Check budget and possibly downgrade tier. Returns resolved tier.

    Replicates the existing budget logic:
    - Under 80%: use tier as-is
    - 80-100%: "fast" -> local Ollama, others stay
    - Over 100%: everything -> local Ollama
    - DB error: fail-closed to local
    """
    try:
        from shared.middleware import check_budget_for_llm_call
        return await check_budget_for_llm_call(tier)
    except (ImportError, OSError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Budget check failed, falling back to local: %s", exc)
        return "local"


# ---------------------------------------------------------------------------
# Cost recording
# ---------------------------------------------------------------------------


async def _record_spend(
    prompt: str,
    result: str,
    system: str,
    tier: str,
    *,
    oj_cost_usd: float | None = None,
    oj_input_tokens: int | None = None,
    oj_output_tokens: int | None = None,
    client_id: int | None = None,
    pipeline_stage: str = "",
):
    """Record LLM spend to budget_tracking table.

    Uses OJ Engine's real cost_usd and token counts when available,
    falls back to char-count estimation otherwise.
    """
    try:
        from shared.db import execute

        if oj_cost_usd is not None and oj_cost_usd > 0:
            cost = oj_cost_usd
            input_tokens = oj_input_tokens or 0
            output_tokens = oj_output_tokens or 0
            total_tokens = input_tokens + output_tokens
        else:
            # Char-count estimation fallback (same as llm_client.py)
            input_tokens = int((len(prompt) + len(system)) / 4)
            output_tokens = int(len(result) / 4)
            total_tokens = input_tokens + output_tokens
            cost_key = _tier_to_cost_key(tier)
            cost = (total_tokens / 1000) * _COST_PER_1K[cost_key]

        if cost >= 0.001:
            month = _SESSION_MONTH
            cost_key = _tier_to_cost_key(tier)
            desc = f"oj-claude-{cost_key} ~{int(total_tokens)}tok"
            if pipeline_stage:
                desc = f"{pipeline_stage}: {desc}"
            await execute(
                """INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
                   VALUES (%s, 'claude_api', %s, %s, %s, %s)""",
                (month, round(cost, 4), desc, client_id, pipeline_stage or None),
            )
    except (OSError, ValueError, TypeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("OJ spend recording failed (non-critical): %s", e)


def _fire_metrics_oj(
    daemon: str,
    model: str,
    call_type: str,
    prompt: str,
    result: str,
    t0: float,
    success: bool,
    error_type: str | None,
    *,
    oj_cost_usd: float | None = None,
    oj_input_tokens: int | None = None,
    oj_output_tokens: int | None = None,
) -> None:
    """Fire-and-forget metrics recording (mirrors LLMClient._fire_metrics)."""
    try:
        from shared.observability import record_llm_call

        latency_ms = int((time.perf_counter() - t0) * 1000)
        input_tokens = oj_input_tokens if oj_input_tokens is not None else max(1, len(prompt) // 4)
        output_tokens = oj_output_tokens if oj_output_tokens is not None else max(0, len(result) // 4)
        cost_usd = oj_cost_usd if oj_cost_usd is not None else (input_tokens * 0.000003 + output_tokens * 0.000015)

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

        from shared.cost_events import CostEvent, emit_cost_event
        from shared.observability import _task_id as _obs_task_id

        cost_event = CostEvent(
            agent_id=daemon,
            model=model,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
            cached_tokens=0,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            task_id=_obs_task_id.get() or None,
            task_type=call_type,
        )
        loop.create_task(emit_cost_event(cost_event))
    except RuntimeError:
        pass  # No running event loop (testing)
    except (ImportError, AttributeError, TypeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("OJ metrics fire failed (non-critical): %s", exc)


# ---------------------------------------------------------------------------
# OJ Engine generate core
# ---------------------------------------------------------------------------


async def _oj_generate(
    prompt: str,
    *,
    system: str = "",
    model: str = "auto",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    json_mode: bool = False,
    client_id: int | None = None,
    pipeline_stage: str = "",
    _budget_pct: float = 100.0,
) -> tuple[str, dict[str, Any]]:
    """Call OJ Engine and return (text, metadata).

    metadata includes: cost_usd, input_tokens, output_tokens, model, finish_reason
    """
    if model == "auto":
        model = "fast"

    engine = _init_oj_engines()
    tier = model

    # Resolve model ID from tier — prefer dynamic selector, fall back to hardcoded
    _selection_obj = None
    _t2_sel = None
    if _MODEL_SELECTOR_AVAILABLE and _select_model_with_t2_fn is not None:
        try:
            _selection_obj, _t2_sel = await _select_model_with_t2_fn(
                tier=model,
                task_type=pipeline_stage or "general",
                budget_remaining_pct=_budget_pct,
            )
            model_id = _selection_obj.model_id
            engine_key = _selection_obj.engine
            logger.info(
                "Model selector chose %s (engine=%s) — %s",
                model_id, engine_key, _selection_obj.reason,
            )
            if _t2_sel is not None and _t2_sel.passes > 1:
                logger.info(
                    "T2 pass@k: %s x%d (%s, verifier=%s)",
                    _t2_sel.model, _t2_sel.passes,
                    _t2_sel.selection_strategy, _t2_sel.verifier,
                )
                # Override model_id with T2-selected model
                model_id = _t2_sel.model
        except (ValueError, KeyError, TypeError, OSError) as sel_exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.warning("Model selector failed, using hardcoded tier map: %s", sel_exc)
            model_id, engine_key = _OJ_TIER_MAP.get(tier, _OJ_TIER_MAP["fast"])
    elif _MODEL_SELECTOR_AVAILABLE and _select_model_fn is not None:
        try:
            _selection_obj = await _select_model_fn(
                tier=model,
                task_type=pipeline_stage or "general",
                budget_remaining_pct=_budget_pct,
            )
            model_id = _selection_obj.model_id
            engine_key = _selection_obj.engine
        except (ValueError, KeyError, TypeError, OSError) as sel_exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.warning("Model selector failed, using hardcoded tier map: %s", sel_exc)
            model_id, engine_key = _OJ_TIER_MAP.get(tier, _OJ_TIER_MAP["fast"])
    else:
        model_id, engine_key = _OJ_TIER_MAP.get(tier, _OJ_TIER_MAP["fast"])

    messages = _make_messages(prompt, system)

    kwargs: dict[str, Any] = {}
    if json_mode:
        try:
            from openjarvis.engine._stubs import ResponseFormat
            kwargs["response_format"] = ResponseFormat(type="json_object")
        except ImportError:
            pass

    # --- T2 pass@k: run multiple passes when T2Selection says so ---
    if (
        _t2_sel is not None
        and _t2_sel.passes > 1
        and _execute_with_t2_fn is not None
    ):
        async def _single_generate() -> str:
            r = await asyncio.to_thread(
                engine.generate,
                messages,
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            return r.get("content", "")

        candidates = await _execute_with_t2_fn(_t2_sel, _single_generate)
        content = candidates[0] if candidates else ""
        metadata: dict[str, Any] = {
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "model": model_id,
            "finish_reason": "stop",
            "t2_passes": _t2_sel.passes,
            "t2_candidates": len(candidates),
            "t2_strategy": _t2_sel.selection_strategy,
        }
        if _selection_obj is not None:
            metadata["selector_reason"] = _selection_obj.reason
            metadata["selector_estimated_cost"] = _selection_obj.estimated_cost * _t2_sel.passes
            metadata["selector_fallback_model"] = _selection_obj.fallback_model
        return content, metadata

    # OJ Engine.generate() is synchronous — run in thread pool
    result = await asyncio.to_thread(
        engine.generate,
        messages,
        model=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )

    content = result.get("content", "")
    usage = result.get("usage", {})
    cost_usd = result.get("cost_usd")
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")

    metadata = {
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": result.get("model", model_id),
        "finish_reason": result.get("finish_reason", "stop"),
    }

    # Attach model selector metadata when available
    if _selection_obj is not None:
        metadata["selector_reason"] = _selection_obj.reason
        metadata["selector_estimated_cost"] = _selection_obj.estimated_cost
        metadata["selector_fallback_model"] = _selection_obj.fallback_model
        if _selection_obj.estimated_cost > 0:
            logger.debug(
                "Estimated cost for this call: $%.6f (selector)",
                _selection_obj.estimated_cost,
            )

    return content, metadata


async def _oj_generate_with_ollama_fallback(
    prompt: str,
    *,
    system: str = "",
    model: str = "auto",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    json_mode: bool = False,
    client_id: int | None = None,
    pipeline_stage: str = "",
    _budget_pct: float = 100.0,
) -> tuple[str, dict[str, Any]]:
    """Try OJ cloud engine, fall back to fallback model on any error.

    When model_selector is available, uses selection.fallback_model instead
    of always falling back to "local".
    """
    try:
        content, meta = await _oj_generate(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            client_id=client_id,
            pipeline_stage=pipeline_stage,
            _budget_pct=_budget_pct,
        )
        return content, meta
    except (OSError, RuntimeError, ValueError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # Determine fallback model: use selector's fallback_model if available
        fallback = "local"
        if _MODEL_SELECTOR_AVAILABLE and _select_model_fn is not None:
            try:
                sel = await _select_model_fn(
                    tier=model,
                    task_type=pipeline_stage or "general",
                    budget_remaining_pct=_budget_pct,
                )
                if sel.fallback_model:
                    fallback = sel.fallback_model
                    logger.warning(
                        "OJ cloud engine failed, using selector fallback %s: %s",
                        fallback, exc,
                    )
                else:
                    logger.warning("OJ cloud engine failed, falling back to local: %s", exc)
            except (ValueError, KeyError, TypeError, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("OJ cloud engine failed, falling back to local: %s", exc)
        else:
            logger.warning("OJ cloud engine failed, falling back to local: %s", exc)

        return await _oj_generate(
            prompt,
            system=system,
            model=fallback,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            client_id=client_id,
            pipeline_stage=pipeline_stage,
            _budget_pct=_budget_pct,
        )


# ---------------------------------------------------------------------------
# Adapter LLMClient — wraps original or routes through OJ
# ---------------------------------------------------------------------------


class LLMClient:
    """Drop-in replacement for shared.llm_client.LLMClient.

    When USE_OJ_ENGINE is true and OJ Engine is available, routes generate()
    calls through the OJ MultiEngine. Otherwise delegates to the original
    LLMClient with zero behavior change.
    """

    def __init__(self):
        # Always keep a reference to the original client for fallback
        from shared.llm_client import LLMClient as _OriginalLLMClient
        self._original = _OriginalLLMClient()
        self._use_oj = _USE_OJ_ENGINE and _OJ_AVAILABLE

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
        """Generate text — identical interface to LLMClient.generate().

        When OJ Engine is active, routes cloud tiers through OJ with budget
        awareness and Ollama fallback. Local tiers always delegate to original.
        """
        if not self._use_oj:
            return await self._original.generate(
                prompt,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                client_id=client_id,
                pipeline_stage=pipeline_stage,
                use_dna=use_dna,
                daemon_name=daemon_name,
            )

        if model == "auto":
            model = "fast"

        # DNA injection: delegate to original's static method
        if use_dna and daemon_name:
            system = self._original._inject_dna(system, daemon_name)

        # Lifecycle injection (Phase 14)
        if daemon_name and os.environ.get("HEARTBEAT_LIFECYCLE_ENABLED", "").lower() in ("true", "1"):
            from pathlib import Path
            lifecycle_path = Path(f"soul/lifecycle/{daemon_name}_lifecycle.md")
            if lifecycle_path.exists():
                lifecycle = lifecycle_path.read_text()
                system = f"{lifecycle}\n\n---\n\n{system}" if system else lifecycle

        t0 = time.perf_counter()

        # Heavy-local and local tiers: delegate to original (they use AirLLM/oLLM paths)
        if model in ("local-heavy", "airllm", "research-local", "ollm", "huge-context-local",
                      "local", "local-small"):
            return await self._original.generate(
                prompt,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                client_id=client_id,
                pipeline_stage=pipeline_stage,
            )

        # No API key: fall back to local via OJ Ollama engine
        from shared.config import config
        if not config.claude.api_key:
            try:
                content, meta = await _oj_generate(
                    prompt, system=system, model="local",
                    max_tokens=max_tokens, temperature=temperature,
                    pipeline_stage=pipeline_stage,
                )
                _fire_metrics_oj(
                    pipeline_stage or "unknown", "local", "generate",
                    prompt, content, t0, True, None,
                )
                return content
            except (OSError, RuntimeError, ConnectionError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                # OJ Ollama also failed — fall through to original
                return await self._original.generate(
                    prompt, system=system, model="local",
                    max_tokens=max_tokens, temperature=temperature,
                    pipeline_stage=pipeline_stage,
                )

        # Budget check — may downgrade to local
        resolved_model = await _check_budget(model)

        if resolved_model in ("local", "local-small"):
            # Budget gate downgraded us — use OJ Ollama
            try:
                content, meta = await _oj_generate(
                    prompt, system=system, model=resolved_model,
                    max_tokens=max_tokens, temperature=temperature,
                    pipeline_stage=pipeline_stage,
                )
                _fire_metrics_oj(
                    pipeline_stage or "unknown", resolved_model, "generate",
                    prompt, content, t0, True, None,
                )
                return content
            except (OSError, RuntimeError, ConnectionError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                return await self._original.generate(
                    prompt, system=system, model=resolved_model,
                    max_tokens=max_tokens, temperature=temperature,
                    pipeline_stage=pipeline_stage,
                )

        # Cloud tier via OJ Engine with per-call Ollama fallback
        try:
            content, meta = await _oj_generate_with_ollama_fallback(
                prompt, system=system, model=resolved_model,
                max_tokens=max_tokens, temperature=temperature,
                client_id=client_id, pipeline_stage=pipeline_stage,
            )
            # Record spend
            await _record_spend(
                prompt, content, system, resolved_model,
                oj_cost_usd=meta.get("cost_usd"),
                oj_input_tokens=meta.get("input_tokens"),
                oj_output_tokens=meta.get("output_tokens"),
                client_id=client_id,
                pipeline_stage=pipeline_stage,
            )
            _fire_metrics_oj(
                pipeline_stage or "unknown", resolved_model, "generate",
                prompt, content, t0, True, None,
                oj_cost_usd=meta.get("cost_usd"),
                oj_input_tokens=meta.get("input_tokens"),
                oj_output_tokens=meta.get("output_tokens"),
            )
            return content
        except (OSError, RuntimeError, ValueError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.warning("OJ Engine fully failed, delegating to original llm_client: %s", exc)
            _fire_metrics_oj(
                pipeline_stage or "unknown", resolved_model, "generate",
                prompt, "", t0, False, type(exc).__name__,
            )
            return await self._original.generate(
                prompt, system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                client_id=client_id, pipeline_stage=pipeline_stage,
            )

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "fast",
        max_tokens: int = 2048,
        temperature: float = 0.3,
        client_id: int | None = None,
        pipeline_stage: str = "",
    ) -> dict:
        """Generate structured JSON output.

        Wraps generate() with json_mode when OJ Engine is active (uses
        response_format), otherwise parses JSON from raw text output.
        """
        if self._use_oj:
            try:
                content, meta = await _oj_generate_with_ollama_fallback(
                    prompt, system=system, model=model,
                    max_tokens=max_tokens, temperature=temperature,
                    json_mode=True,
                    client_id=client_id, pipeline_stage=pipeline_stage,
                )
                await _record_spend(
                    prompt, content, system, model,
                    oj_cost_usd=meta.get("cost_usd"),
                    oj_input_tokens=meta.get("input_tokens"),
                    oj_output_tokens=meta.get("output_tokens"),
                    client_id=client_id,
                    pipeline_stage=pipeline_stage,
                )
                return json.loads(content)
            except json.JSONDecodeError:
                # OJ returned non-JSON — try to extract
                return _extract_json(content)
            except (OSError, RuntimeError, ConnectionError, TimeoutError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning("OJ generate_json failed, falling back to text parse: %s", exc)

        # Fallback: generate text and parse JSON
        json_system = (system + "\n\n" if system else "") + "Respond with valid JSON only. No markdown, no explanation."
        raw = await self.generate(
            prompt, system=json_system, model=model,
            max_tokens=max_tokens, temperature=temperature,
            client_id=client_id, pipeline_stage=pipeline_stage,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return _extract_json(raw)

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
        """Generate text from prompt + images — delegates to original (OJ Engine
        does not add value for multimodal; Anthropic API is already used directly)."""
        return await self._original.generate_with_images(
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
        """Quick classification using fast model — delegates to original."""
        return await self._original.classify(text, categories)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama — delegates to original."""
        return await self._original.embed(text)

    async def close(self):
        """Close HTTP clients and OJ engines."""
        await self._original.close()
        if self._use_oj and _oj_multi_engine is not None:
            try:
                _oj_multi_engine.close()
            except (OSError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from LLM output that may contain markdown fences."""
    # Try stripping markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Last resort: return as a wrapped dict
    return {"raw": text, "_parse_error": True}


# ---------------------------------------------------------------------------
# Singleton — same export name as shared.llm_client
# ---------------------------------------------------------------------------

llm = LLMClient()
