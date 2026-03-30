"""Quality comparison tests: enriched component prompts vs text-only.

Proves that variant prompts with real component snippets carry more structural
context than the text-only "21st.dev-style" references.
"""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock


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
    _stub_heavy_deps()
    sys.modules.pop("clawdbot.site_builder", None)
    return importlib.import_module("clawdbot.site_builder")


# ---------------------------------------------------------------------------
# Test 1: Component-enriched prompt is richer than text-only
# ---------------------------------------------------------------------------
def test_component_enriched_prompt_is_richer_than_text_only():
    sb = _load_site_builder()

    # Text-only: no component snippets
    text_only_block = sb._format_component_snippets_block([])

    # Component-enriched: real snippets
    snippets = [
        {
            "section": "hero",
            "code": (
                "<div className='flex items-center justify-between px-6 py-12'>"
                "<div className='max-w-xl'>"
                "<h1 className='text-5xl font-bold tracking-tight'>Headline</h1>"
                "<p className='mt-4 text-lg text-gray-600'>Subheadline text</p>"
                "<button className='mt-6 bg-blue-600 px-8 py-3 text-white rounded-lg'>"
                "Get Started</button></div>"
                "<div className='hidden lg:block'><img src='/hero.jpg' alt='Hero' /></div>"
                "</div>"
            ),
        },
        {
            "section": "cta",
            "code": (
                "<section className='bg-blue-50 py-16 text-center'>"
                "<h2 className='text-3xl font-semibold'>Ready to start?</h2>"
                "<button className='mt-8 bg-blue-600 px-10 py-4 text-white rounded-xl'>"
                "Book Now</button></section>"
            ),
        },
    ]
    enriched_block = sb._format_component_snippets_block(snippets)

    # Text-only is empty string
    assert text_only_block == ""

    # Enriched has structural patterns
    assert "COMPONENT PATTERNS" in enriched_block
    assert "flex items-center" in enriched_block
    assert "HERO PATTERN" in enriched_block
    assert "CTA PATTERN" in enriched_block
    assert len(enriched_block) > 200  # Substantial structural content


# ---------------------------------------------------------------------------
# Test 2: Enriched prompt carries Tailwind class patterns for adaptation
# ---------------------------------------------------------------------------
def test_component_snippets_carry_tailwind_patterns():
    sb = _load_site_builder()

    snippets = [
        {
            "section": "services",
            "code": (
                "<div className='grid grid-cols-1 md:grid-cols-3 gap-6 px-8'>"
                "<div className='rounded-xl border p-6 shadow-sm'>"
                "<h3 className='text-xl font-semibold'>Service One</h3>"
                "<p className='mt-2 text-gray-500'>Description</p>"
                "</div></div>"
            ),
        },
    ]

    block = sb._format_component_snippets_block(snippets)

    # Contains Tailwind patterns that LLM can adapt to HTML
    assert "grid-cols" in block
    assert "rounded-xl" in block
    assert "shadow-sm" in block
    assert "NOT React copy" in block


# ---------------------------------------------------------------------------
# Test 3: Empty snippets produce no block (no prompt bloat)
# ---------------------------------------------------------------------------
def test_no_snippets_produces_empty_block():
    sb = _load_site_builder()

    assert sb._format_component_snippets_block([]) == ""
    assert sb._format_component_snippets_block(None or []) == ""
