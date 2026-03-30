"""Regression tests for full-site QA before deployment."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch


def run(coro):
    return asyncio.run(coro)


def load_build_site_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.get_config = AsyncMock(return_value=None)

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    sys.modules.pop("titan.pipeline.build_site", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state_machine
    return importlib.import_module("titan.pipeline.build_site")


def _stub_site_builder_deps():
    """Stub heavy deps so site_builder can be imported."""
    for mod_name in (
        "shared.db",
        "shared.comms",
        "shared.llm_client",
        "shared.anti_slop",
        "shared.execution_loop",
        "clawdbot.site_quality",
        "clawdbot.daemon",
    ):
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            stub.__dict__.setdefault("emit_event", AsyncMock())
            stub.__dict__.setdefault("record_decision", AsyncMock())
            stub.__dict__.setdefault("llm", MagicMock())
            stub.__dict__.setdefault("AntiSlopScorer", MagicMock)
            stub.__dict__.setdefault("detect_secrets", MagicMock(return_value=[]))
            stub.__dict__.setdefault("record_quality_score", AsyncMock())
            stub.__dict__.setdefault("rewrite_loop", AsyncMock())
            stub.__dict__.setdefault("is_enabled", MagicMock(return_value=False))
            stub.__dict__.setdefault("evaluate_site_experience", AsyncMock(return_value={}))
            stub.__dict__.setdefault("handle_image_generation", AsyncMock(return_value={}))
            sys.modules[mod_name] = stub


def _load_site_builder():
    _stub_site_builder_deps()
    sys.modules.pop("clawdbot.site_builder", None)
    return importlib.import_module("clawdbot.site_builder")


def test_build_sites_blocks_deployment_when_full_site_qa_fails():
    build_site = load_build_site_module()

    lead = {
        "id": 9,
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "research_summary": "",
        "research_facts": "",
        "language": "en",
        "country": "Mexico",
        "city": "Mazatlan",
        "demo_site_url": "https://demo.example",
    }

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.request_task_result = AsyncMock(
        return_value={"ok": True, "result": {"passed": False, "reason": "no_placeholder_assets"}}
    )
    # Save any real module so we can restore after test
    _saved_comms = sys.modules.get("shared.comms")
    sys.modules["shared.comms"] = fake_comms

    try:
        with patch.object(build_site, "fetch_all", AsyncMock(return_value=[lead])):
            with patch.object(build_site, "_build_full_site", AsyncMock(return_value="https://final.example")):
                with patch.object(build_site, "emit_event", AsyncMock()) as emit_event:
                    run(build_site.build_sites())

        build_site.execute.assert_not_awaited()
        blocked_calls = [call for call in emit_event.await_args_list if call.args and call.args[0] == "site_deploy_blocked"]
        assert blocked_calls
    finally:
        # Restore sys.modules to prevent contaminating subsequent test files
        if _saved_comms is not None:
            sys.modules["shared.comms"] = _saved_comms
        else:
            sys.modules.pop("shared.comms", None)


# ---------------------------------------------------------------------------
# New tests for pipeline integration (10-02)
# ---------------------------------------------------------------------------

def test_pipeline_degrades_gracefully_without_21st_dev():
    """When resolve_design_sources_with_components raises, pipeline falls back to text-only."""
    sb = _load_site_builder()

    lead = {"industry": "dentist"}

    # Make enriched version raise
    with patch(
        "clawdbot.design_sources.resolve_design_sources_with_components",
        AsyncMock(side_effect=RuntimeError("21st.dev API unavailable")),
    ):
        result = run(sb._resolve_design_sources_async(lead))

    # Should fall back to sync text-only sources
    assert "sources" in result
    assert "adaptation_rules" in result
    # Sources should exist (from curated catalog)
    assert isinstance(result["sources"], list)


def test_asset_urls_appear_in_variant_context():
    """Generated asset URLs from _generate_asset_pack flow into variant prompt context."""
    sb = _load_site_builder()

    asset_pack = {
        "logo_url": "https://recraft.ai/generated-logo.png",
        "hero_url": "https://recraft.ai/generated-hero.png",
        "assets_generated": 2,
    }

    build_plan = {
        "runtime_profile": "dom-motion",
        "page_count": 1,
        "build_mode": "conversion",
        "design_sources": [],
        "cdn_deps": [],
        "runtime_hints": [],
        "fallback_rules": [],
    }

    direction = {
        "name": "minimal-geometric",
        "style": "Swiss minimalism",
        "colors": "Near-black + white",
        "fonts": "Satoshi + IBM Plex Sans",
        "layout": "Centered single-column",
        "animation": "Subtle fade-ins",
        "copy_angle": "Direct, confident",
    }

    brief = "PRODUCT BRIEF: Test Business\nIndustry: dentist\n"

    # Mock llm.generate to return HTML
    mock_llm = AsyncMock(return_value="<!DOCTYPE html><html><body><h1>Test</h1></body></html>")
    sys.modules["shared.llm_client"].llm.generate = mock_llm

    result = run(sb._build_one_variant(direction, brief, asset_pack, build_plan))

    # Check that the prompt sent to LLM contains the asset URLs
    call_args = mock_llm.call_args
    prompt_sent = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt", "")
    assert "generated-logo.png" in prompt_sent
    assert "generated-hero.png" in prompt_sent


def test_component_snippets_flow_through_build_plan():
    """Component snippets from design_sources reach the variant prompt via build_plan."""
    sb = _load_site_builder()

    asset_pack = {"logo_url": "", "hero_url": "", "assets_generated": 0}

    build_plan = {
        "runtime_profile": "dom-motion",
        "page_count": 1,
        "build_mode": "conversion",
        "design_sources": [
            {
                "source": "21st.dev",
                "title": "Split Hero CTA",
                "sections": ["hero"],
                "component_snippets": [
                    {
                        "section": "hero",
                        "code": "<div className='flex items-center gap-8 px-12 py-20'><h1>Bold Hero</h1></div>",
                    }
                ],
            }
        ],
        "cdn_deps": [],
        "runtime_hints": [],
        "fallback_rules": [],
    }

    direction = {
        "name": "minimal-geometric",
        "style": "Swiss minimalism",
        "colors": "Near-black + white",
        "fonts": "Satoshi + IBM Plex Sans",
        "layout": "Centered single-column",
        "animation": "Subtle fade-ins",
        "copy_angle": "Direct, confident",
    }

    brief = "PRODUCT BRIEF: Test Business\nIndustry: dentist\n"

    mock_llm = AsyncMock(return_value="<!DOCTYPE html><html><body><h1>Test</h1></body></html>")
    sys.modules["shared.llm_client"].llm.generate = mock_llm

    run(sb._build_one_variant(direction, brief, asset_pack, build_plan))

    # Check that the prompt contains the component snippet
    call_args = mock_llm.call_args
    prompt_sent = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt", "")
    assert "COMPONENT PATTERNS" in prompt_sent
    assert "flex items-center" in prompt_sent
    assert "HERO PATTERN" in prompt_sent
