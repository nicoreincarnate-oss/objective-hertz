import importlib
import os
import sys
from unittest.mock import patch


def load_policy(**env_overrides):
    sys.modules.pop("shared.airllm_policy", None)
    sys.modules.pop("shared.config", None)
    with patch.dict(os.environ, env_overrides, clear=False):
        return importlib.import_module("shared.airllm_policy")


def test_airllm_prefers_research_and_memory_stages():
    policy = load_policy(AIRLLM_ENABLED="true")

    assert policy.should_route_to_airllm(
        requested_model="local",
        pipeline_stage="research:papers",
        prompt="short prompt",
    ) is True
    assert policy.should_route_to_airllm(
        requested_model="smart",
        pipeline_stage="sleep_cycle_alpha",
        prompt="short prompt",
    ) is True


def test_airllm_skips_fast_small_and_embed_tiers():
    policy = load_policy(AIRLLM_ENABLED="true")

    for tier in ("fast", "local-small", "embed"):
        assert policy.should_route_to_airllm(
            requested_model=tier,
            pipeline_stage="research:papers",
            prompt="x" * 50000,
        ) is False


def test_airllm_uses_prompt_size_threshold_for_local_heavy_work():
    policy = load_policy(
        AIRLLM_ENABLED="true",
        AIRLLM_PROMPT_CHAR_THRESHOLD="100",
    )

    assert policy.should_route_to_airllm(
        requested_model="local",
        pipeline_stage="",
        prompt="x" * 120,
    ) is True
    assert policy.explain_airllm_routing(
        requested_model="local",
        pipeline_stage="",
        prompt="x" * 120,
    ) == "airllm_heavy_async_fit"


def test_airllm_forced_model_tiers_always_route_when_enabled():
    policy = load_policy(AIRLLM_ENABLED="true")

    assert policy.should_route_to_airllm(requested_model="local-heavy") is True
    assert policy.should_route_to_airllm(requested_model="airllm") is True
    assert policy.should_route_to_airllm(requested_model="research-local") is True


def test_ollm_wins_for_ultra_large_context_thresholds():
    policy = load_policy(
        AIRLLM_ENABLED="true",
        AIRLLM_PROMPT_CHAR_THRESHOLD="20000",
        OLLM_ENABLED="true",
        OLLM_PROMPT_CHAR_THRESHOLD="1000",
    )

    assert policy.choose_heavy_local_backend(
        requested_model="local-heavy",
        pipeline_stage="",
        prompt="x" * 1500,
    ) == "ollm"
    assert policy.explain_heavy_local_routing(
        requested_model="local-heavy",
        pipeline_stage="",
        prompt="x" * 1500,
    ) == "ollm_large_context_fit"


def test_ollm_forced_tier_routes_directly_when_enabled():
    policy = load_policy(OLLM_ENABLED="true")

    assert policy.choose_heavy_local_backend(requested_model="ollm") == "ollm"
    assert policy.choose_heavy_local_backend(requested_model="huge-context-local") == "ollm"
