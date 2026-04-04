"""Heavy-local inference routing for AirLLM and oLLM.

AirLLM is the default heavyweight local lane for async research and memory
tasks. oLLM is the preferred donor for ultra-large offline contexts such as
huge logs, contracts, or very large document passes.
"""

from __future__ import annotations

from shared.config import config

_DEFAULT_AIRLLM_STAGE_KEYWORDS = (
    "research",
    "intel",
    "scout",
    "paper",
    "memory",
    "sleep_cycle",
    "digest",
    "summary",
    "magma",
)
_DEFAULT_OLLM_STAGE_KEYWORDS = (
    "deep_research",
    "huge_context",
    "log_analysis",
    "contract_analysis",
    "compliance",
    "screen_digest",
)

_NON_HEAVY_LOCAL_MODELS = {"fast", "local-small", "embed"}
_FORCED_AIRLLM_MODELS = {"airllm", "research-local"}
_FORCED_OLLM_MODELS = {"ollm", "huge-context-local"}
_FORCED_HEAVY_LOCAL_MODELS = {"local-heavy"} | _FORCED_AIRLLM_MODELS | _FORCED_OLLM_MODELS


def _keyword_list(raw: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return defaults
    values = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return values or defaults


def airllm_stage_keywords() -> tuple[str, ...]:
    return _keyword_list(config.airllm.preferred_stages, _DEFAULT_AIRLLM_STAGE_KEYWORDS)


def ollm_stage_keywords() -> tuple[str, ...]:
    return _keyword_list(config.ollm.preferred_stages, _DEFAULT_OLLM_STAGE_KEYWORDS)


def should_route_to_heavy_local(
    *,
    requested_model: str,
    pipeline_stage: str = "",
    prompt: str = "",
    system: str = "",
) -> bool:
    normalized_model = (requested_model or "").strip().lower()
    if normalized_model in _FORCED_HEAVY_LOCAL_MODELS:
        return True
    if normalized_model in _NON_HEAVY_LOCAL_MODELS:
        return False

    normalized_stage = (pipeline_stage or "").strip().lower()
    total_chars = len(prompt or "") + len(system or "")

    if config.airllm.enabled and normalized_stage and any(keyword in normalized_stage for keyword in airllm_stage_keywords()):
        return True
    if config.ollm.enabled and normalized_stage and any(keyword in normalized_stage for keyword in ollm_stage_keywords()):
        return True
    if config.airllm.enabled and total_chars >= config.airllm.prompt_char_threshold:
        return True
    if config.ollm.enabled and total_chars >= config.ollm.prompt_char_threshold:
        return True
    return False


def choose_heavy_local_backend(
    *,
    requested_model: str,
    pipeline_stage: str = "",
    prompt: str = "",
    system: str = "",
) -> str:
    """Pick the best heavy-local backend: ollm, airllm, or none."""
    normalized_model = (requested_model or "").strip().lower()
    normalized_stage = (pipeline_stage or "").strip().lower()
    total_chars = len(prompt or "") + len(system or "")

    if normalized_model in _FORCED_OLLM_MODELS and config.ollm.enabled:
        return "ollm"
    if normalized_model in _FORCED_AIRLLM_MODELS and config.airllm.enabled:
        return "airllm"
    if normalized_model == "local-heavy":
        if config.ollm.enabled and (
            (normalized_stage and any(keyword in normalized_stage for keyword in ollm_stage_keywords()))
            or total_chars >= config.ollm.prompt_char_threshold
        ):
            return "ollm"
        if config.airllm.enabled:
            return "airllm"
        if config.ollm.enabled:
            return "ollm"
        return "none"

    if normalized_model in _NON_HEAVY_LOCAL_MODELS:
        return "none"

    if config.ollm.enabled:
        if normalized_stage and any(keyword in normalized_stage for keyword in ollm_stage_keywords()):
            return "ollm"
        if total_chars >= config.ollm.prompt_char_threshold:
            return "ollm"

    if config.airllm.enabled:
        if normalized_stage and any(keyword in normalized_stage for keyword in airllm_stage_keywords()):
            return "airllm"
        if total_chars >= config.airllm.prompt_char_threshold:
            return "airllm"

    return "none"


def explain_heavy_local_routing(
    *,
    requested_model: str,
    pipeline_stage: str = "",
    prompt: str = "",
    system: str = "",
) -> str:
    backend = choose_heavy_local_backend(
        requested_model=requested_model,
        pipeline_stage=pipeline_stage,
        prompt=prompt,
        system=system,
    )
    normalized_model = (requested_model or "").strip().lower()
    if normalized_model in _NON_HEAVY_LOCAL_MODELS:
        return "lightweight_tier"
    if backend == "ollm":
        if normalized_model in _FORCED_OLLM_MODELS:
            return "explicit_ollm_tier"
        return "ollm_large_context_fit"
    if backend == "airllm":
        if normalized_model in _FORCED_AIRLLM_MODELS:
            return "explicit_airllm_tier"
        return "airllm_heavy_async_fit"
    return "standard_local_path"


def should_route_to_airllm(
    *,
    requested_model: str,
    pipeline_stage: str = "",
    prompt: str = "",
    system: str = "",
) -> bool:
    return choose_heavy_local_backend(
        requested_model=requested_model,
        pipeline_stage=pipeline_stage,
        prompt=prompt,
        system=system,
    ) == "airllm"


def explain_airllm_routing(
    *,
    requested_model: str,
    pipeline_stage: str = "",
    prompt: str = "",
    system: str = "",
) -> str:
    if not config.airllm.enabled:
        return "airllm_disabled"
    if should_route_to_airllm(
        requested_model=requested_model,
        pipeline_stage=pipeline_stage,
        prompt=prompt,
        system=system,
    ):
        return explain_heavy_local_routing(
            requested_model=requested_model,
            pipeline_stage=pipeline_stage,
            prompt=prompt,
            system=system,
        )
    return "other_heavy_backend_or_standard_path"
