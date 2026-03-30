"""Tests for Recraft asset generation pipeline in site_builder.

Covers: _generate_asset_pack budget gating, asset generation with
handle_image_generation, budget tracking, hero threshold, failure resilience.
"""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch


def run(coro):
    return asyncio.run(coro)


def _stub_heavy_deps():
    """Stub heavy dependencies so site_builder imports cleanly."""
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
            # Give each stub basic attributes that site_builder expects
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

    # Ensure clawdbot.design_sources is available (real module)
    if "clawdbot.design_sources" in sys.modules:
        pass  # keep real module


def _load_site_builder():
    """Import site_builder with stubs in place."""
    _stub_heavy_deps()
    sys.modules.pop("clawdbot.site_builder", None)
    return importlib.import_module("clawdbot.site_builder")


# ---------------------------------------------------------------------------
# Test 1: _generate_asset_pack returns logo+hero when allow_paid_assets=True
# ---------------------------------------------------------------------------
def test_asset_pack_generates_logo_and_hero_when_allowed():
    sb = _load_site_builder()

    mock_handle = AsyncMock(side_effect=[
        {"url": "https://recraft.ai/logo.png", "prompt": "logo"},  # logo
        {"url": "https://recraft.ai/hero.png", "prompt": "hero"},  # hero
    ])

    lead = {"id": 1, "business_name": "TestBiz", "industry": "dentist", "city": "Austin"}
    build_plan = {
        "allow_paid_assets": True,
        "max_assets": 2,
        "remaining_site_asset_budget": 0.08,
        "site_type": "full",
        "build_mode": "conversion",
    }

    with patch("clawdbot.daemon.handle_image_generation", mock_handle):
        result = run(sb._generate_asset_pack(lead, build_plan))

    assert result["logo_url"] == "https://recraft.ai/logo.png"
    assert result["hero_url"] == "https://recraft.ai/hero.png"
    assert result["assets_generated"] == 2


# ---------------------------------------------------------------------------
# Test 2: _generate_asset_pack returns empty with reason when budget blocked
# ---------------------------------------------------------------------------
def test_asset_pack_blocked_by_budget_guard():
    sb = _load_site_builder()

    lead = {"id": 2, "business_name": "BudgetBiz", "industry": "plumber"}
    build_plan = {"allow_paid_assets": False}

    result = run(sb._generate_asset_pack(lead, build_plan))

    assert result["logo_url"] == ""
    assert result["hero_url"] == ""
    assert result["reason"] == "budget_guard"
    assert result["assets_generated"] == 0


# ---------------------------------------------------------------------------
# Test 3: _generate_asset_pack tracks assets_generated count
# ---------------------------------------------------------------------------
def test_asset_pack_tracks_generated_count():
    sb = _load_site_builder()

    # Only logo succeeds, hero returns empty
    mock_handle = AsyncMock(side_effect=[
        {"url": "https://recraft.ai/logo.png"},  # logo succeeds
        {"url": ""},  # hero fails (empty url)
    ])

    lead = {"id": 3, "business_name": "CountBiz", "industry": "restaurant"}
    build_plan = {
        "allow_paid_assets": True,
        "max_assets": 2,
        "remaining_site_asset_budget": 0.08,
        "site_type": "full",
    }

    with patch("clawdbot.daemon.handle_image_generation", mock_handle):
        result = run(sb._generate_asset_pack(lead, build_plan))

    assert result["assets_generated"] == 1
    assert result["logo_url"] == "https://recraft.ai/logo.png"
    assert result["hero_url"] == ""


# ---------------------------------------------------------------------------
# Test 4: Hero generation respects remaining_site_asset_budget >= 0.02
# ---------------------------------------------------------------------------
def test_hero_blocked_when_budget_too_low():
    sb = _load_site_builder()

    mock_handle = AsyncMock(return_value={"url": "https://recraft.ai/logo.png"})

    lead = {"id": 4, "business_name": "LowBudget", "industry": "dentist"}
    build_plan = {
        "allow_paid_assets": True,
        "max_assets": 2,
        "remaining_site_asset_budget": 0.01,  # below 0.02 threshold
        "site_type": "full",
    }

    with patch("clawdbot.daemon.handle_image_generation", mock_handle):
        result = run(sb._generate_asset_pack(lead, build_plan))

    # Logo generated but hero skipped due to low budget
    assert result["logo_url"] == "https://recraft.ai/logo.png"
    assert result["hero_url"] == ""
    assert result["assets_generated"] == 1


# ---------------------------------------------------------------------------
# Test 5: Recraft failure is non-fatal (returns empty URL, does not raise)
# ---------------------------------------------------------------------------
def test_asset_pack_recraft_failure_non_fatal():
    sb = _load_site_builder()

    mock_handle = AsyncMock(return_value={"url": "", "error": "API timeout"})

    lead = {"id": 5, "business_name": "FailBiz", "industry": "plumber"}
    build_plan = {
        "allow_paid_assets": True,
        "max_assets": 2,
        "remaining_site_asset_budget": 0.08,
        "site_type": "full",
    }

    with patch("clawdbot.daemon.handle_image_generation", mock_handle):
        result = run(sb._generate_asset_pack(lead, build_plan))

    # No assets generated, but no exception raised
    assert result["assets_generated"] == 0
    assert result["logo_url"] == ""
    assert result["hero_url"] == ""


# ---------------------------------------------------------------------------
# Test 6: _resolve_design_sources uses enriched components when available
# ---------------------------------------------------------------------------
def test_resolve_design_sources_calls_enriched_version():
    sb = _load_site_builder()

    enriched_result = {
        "sources": [
            {
                "source": "21st.dev",
                "title": "Split Hero CTA",
                "sections": ["hero", "cta"],
                "component_snippets": [
                    {"section": "hero", "code": "<div class='hero'>...</div>"}
                ],
            }
        ],
        "adaptation_rules": ["Use shadcn/ui as spine."],
    }

    lead = {"industry": "dentist"}

    with patch(
        "clawdbot.site_builder.resolve_design_sources_with_components",
        new_callable=lambda: AsyncMock,
        return_value=enriched_result,
    ):
        result = run(sb._resolve_design_sources_async(lead))

    assert result["sources"][0].get("component_snippets") is not None
    assert len(result["sources"][0]["component_snippets"]) >= 1


# ---------------------------------------------------------------------------
# Test 7: Component snippets appear in variant prompt context
# ---------------------------------------------------------------------------
def test_component_snippets_in_variant_prompt():
    sb = _load_site_builder()

    # Build a brief that contains component snippets
    component_snippets = [
        {"section": "hero", "code": "<div className='flex items-center'><h1>Hero</h1></div>"},
        {"section": "cta", "code": "<button className='bg-blue-500'>Get Started</button>"},
    ]

    brief_with_components = sb._format_component_snippets_block(component_snippets)

    assert "COMPONENT PATTERNS" in brief_with_components
    assert "hero" in brief_with_components.lower()
    assert "flex items-center" in brief_with_components
    assert "NOT React copy" in brief_with_components or "inspiration" in brief_with_components.lower()
