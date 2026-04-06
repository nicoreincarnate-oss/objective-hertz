"""Tests for Phase 33 DesignTokens data model + CDN stack."""

from __future__ import annotations

from clawdbot.design_tokens import (
    CDN_STACK,
    DIRECTION_CDN_MAP,
    DesignTokens,
    build_head_block,
    extract_tokens_from_direction,
    extract_tokens_from_taste,
    tokens_to_css_vars,
    tokens_to_prompt_block,
    tokens_to_tailwind_config,
)

# All 11 directions: 5 original + 6 taste-derived
ALL_DIRECTIONS = [
    {"name": "minimal-geometric", "colors": "Near-black + white + single coral accent", "fonts": "Satoshi (display) + IBM Plex Sans (body)"},
    {"name": "bold-editorial", "colors": "Rich navy + warm cream + gold accent", "fonts": "Instrument Serif (display) + Source Serif Pro (body)"},
    {"name": "dark-cinematic", "colors": "True black + silver/chrome + electric blue glow", "fonts": "Clash Display (display) + Outfit (body)"},
    {"name": "organic-illustrated", "colors": "Warm sand + forest green + terracotta accent", "fonts": "Fraunces (display) + Work Sans (body)"},
    {"name": "playful-animated", "colors": "Indigo + hot pink + lime accent on white", "fonts": "Cabinet Grotesk (display) + Plus Jakarta Sans (body)"},
    {"name": "immersive-3d"},
    {"name": "audio-reactive-canvas"},
    {"name": "cosmos-dark-curation"},
    {"name": "arena-monastic-grid"},
    {"name": "yokoo-psychedelic-maximalism"},
    {"name": "fukuda-optical-precision"},
]


def test_extract_tokens_all_directions():
    """Verify all 11 directions produce valid DesignTokens with hex colors."""
    for direction in ALL_DIRECTIONS:
        tokens = extract_tokens_from_direction(direction)
        assert isinstance(tokens, DesignTokens)
        assert tokens.direction_name == direction["name"]
        # Colors should be hex strings
        assert tokens.primary.startswith("#"), f"{direction['name']}: primary not hex"
        assert tokens.accent.startswith("#"), f"{direction['name']}: accent not hex"
        assert tokens.background.startswith("#"), f"{direction['name']}: background not hex"
        # Fonts should not be empty
        assert tokens.font_display, f"{direction['name']}: missing display font"
        assert tokens.font_body, f"{direction['name']}: missing body font"
        # CDN deps should be populated
        assert isinstance(tokens.cdn_deps, list)


def test_css_vars_generation():
    """Verify CSS custom properties format is correct."""
    tokens = extract_tokens_from_direction(ALL_DIRECTIONS[0])
    css = tokens_to_css_vars(tokens)
    assert ":root {" in css
    assert "--color-primary:" in css
    assert "--color-accent:" in css
    assert "--color-bg:" in css
    assert "--color-surface:" in css
    assert "--color-text:" in css
    assert "--color-text-secondary:" in css
    assert "--color-text-muted:" in css
    assert "--font-display:" in css
    assert "--font-body:" in css
    assert "--scale-ratio:" in css
    assert "--section-py:" in css
    assert "--radius-sm:" in css
    assert "--shadow:" in css
    assert "--transition-duration:" in css
    assert css.strip().endswith("}")


def test_head_block_generation():
    """Verify complete HTML head block."""
    tokens = extract_tokens_from_direction({"name": "dark-cinematic"})
    head = build_head_block(tokens)
    assert "<head>" in head
    assert "</head>" in head
    assert 'charset="UTF-8"' in head
    assert "viewport" in head
    assert "robots" in head
    # Font links
    assert "fonts.googleapis.com" in head or "fontshare.com" in head
    # Tailwind
    assert "tailwindcss" in head
    # Alpine
    assert "alpinejs" in head
    # Direction CDN deps (dark-cinematic includes gsap, motion)
    assert "gsap" in head
    # CSS vars
    assert "--color-primary:" in head
    # Reduced motion media query
    assert "prefers-reduced-motion" in head


def test_cdn_stack_completeness():
    """Verify all expected CDN entries present and are URLs."""
    expected_keys = [
        "tailwind", "alpine", "gsap", "scrolltrigger", "motion",
        "lenis", "lottie", "rive", "threejs", "spline", "p5",
        "tonejs", "pixijs",
    ]
    for key in expected_keys:
        assert key in CDN_STACK, f"Missing CDN entry: {key}"
        assert CDN_STACK[key].startswith("https://"), f"CDN URL invalid: {key}"


def test_direction_cdn_map_completeness():
    """All 11 directions should have CDN mappings."""
    for direction in ALL_DIRECTIONS:
        name = direction["name"]
        assert name in DIRECTION_CDN_MAP, f"Missing DIRECTION_CDN_MAP entry: {name}"
        deps = DIRECTION_CDN_MAP[name]
        assert isinstance(deps, list)
        assert len(deps) > 0, f"Empty CDN deps for: {name}"
        # All dep keys should exist in CDN_STACK
        for dep in deps:
            assert dep in CDN_STACK, f"CDN dep '{dep}' for '{name}' not in CDN_STACK"


def test_extract_tokens_from_taste():
    """Verify taste profile overlay merges colors."""
    direction = {"name": "minimal-geometric"}
    profile = {
        "color_palettes": [
            {"colors": ["#AA0000", "#00BB00", "#0000CC", "#DDDDDD", "#EEEEEE"], "mood": "dark"},
        ]
    }
    tokens = extract_tokens_from_taste(profile, direction)
    assert tokens.primary == "#AA0000"
    assert tokens.secondary == "#00BB00"
    assert tokens.accent == "#0000CC"
    assert tokens.background == "#DDDDDD"
    assert tokens.surface == "#EEEEEE"
    # Font should still come from direction
    assert tokens.font_display == "Satoshi"


def test_extract_tokens_from_taste_empty():
    """Taste with no palettes should return direction defaults."""
    direction = {"name": "bold-editorial"}
    tokens_base = extract_tokens_from_direction(direction)
    tokens_taste = extract_tokens_from_taste({}, direction)
    assert tokens_taste.primary == tokens_base.primary
    assert tokens_taste.accent == tokens_base.accent


def test_prompt_block_generation():
    """Verify prompt block contains expected sections."""
    tokens = extract_tokens_from_direction({"name": "playful-animated"})
    block = tokens_to_prompt_block(tokens)
    assert "DESIGN TOKENS" in block
    assert "Colors:" in block
    assert "Typography:" in block
    assert "Spacing:" in block
    assert "Motion:" in block
    assert "var(--color-primary)" in block


def test_tailwind_config_generation():
    """Verify Tailwind config output."""
    tokens = extract_tokens_from_direction({"name": "minimal-geometric"})
    config = tokens_to_tailwind_config(tokens)
    assert "@theme" in config
    assert "--color-primary:" in config
    assert "--font-display:" in config
