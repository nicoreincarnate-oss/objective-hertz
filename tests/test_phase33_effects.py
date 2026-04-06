"""Tests for Phase 33 effects translation layer."""

from __future__ import annotations

from clawdbot.effects import (
    get_effect_css,
    inject_effect,
    list_effects,
)


def test_all_effects_listed():
    """Verify 15 effects are registered."""
    effects = list_effects()
    assert len(effects) == 15, f"Expected 15 effects, found {len(effects)}"

    expected = [
        "fade_up_scroll", "stagger_children", "parallax_bg",
        "split_text_reveal", "pin_scrub", "spring_hover",
        "layout_animate", "exit_animate", "number_counter",
        "text_shimmer", "spotlight_cursor", "magnetic_button",
        "marquee_ticker", "card_tilt_3d", "p5_particles",
    ]
    for name in expected:
        assert name in effects, f"Missing effect: {name}"


def test_inject_effect_all():
    """Every effect should return a non-empty <script> block."""
    for name in list_effects():
        result = inject_effect(name, ".test-selector")
        assert "<script" in result, f"Effect '{name}' did not return a <script> block"
        assert "prefers-reduced-motion" in result, f"Effect '{name}' missing reduced-motion check"


def test_inject_effect_unknown():
    """Unknown effect name should return empty string."""
    result = inject_effect("nonexistent_effect", ".test")
    assert result == ""


def test_inject_effect_with_config():
    """Config overrides should be passed through."""
    result = inject_effect("fade_up_scroll", ".card", {"y": 60, "duration": 1.2})
    assert "60" in result
    assert "1.2" in result


def test_effect_css_shimmer():
    """Text shimmer should have CSS keyframes."""
    css = get_effect_css("text_shimmer")
    assert "<style>" in css
    assert "@keyframes shimmer" in css


def test_effect_css_marquee():
    """Marquee ticker should have CSS keyframes."""
    css = get_effect_css("marquee_ticker")
    assert "<style>" in css
    assert "@keyframes marquee-scroll" in css


def test_effect_css_none():
    """JS-only effects should return empty CSS."""
    assert get_effect_css("fade_up_scroll") == ""
    assert get_effect_css("number_counter") == ""
    assert get_effect_css("nonexistent") == ""


def test_gsap_effects_use_register_plugin():
    """GSAP effects should register ScrollTrigger plugin."""
    for name in ["fade_up_scroll", "stagger_children", "parallax_bg", "split_text_reveal", "pin_scrub"]:
        result = inject_effect(name, ".el")
        assert "gsap.registerPlugin" in result, f"Effect '{name}' missing gsap.registerPlugin"


def test_motion_effects_use_module():
    """Motion effects should use type=module for ESM import."""
    for name in ["spring_hover", "layout_animate", "exit_animate"]:
        result = inject_effect(name, ".el")
        assert 'type="module"' in result, f"Effect '{name}' missing type=module"


def test_p5_particles_effect():
    """p5 particles should reference p5 constructor."""
    result = inject_effect("p5_particles", "#hero")
    assert "new p5" in result
    assert "#hero" in result
