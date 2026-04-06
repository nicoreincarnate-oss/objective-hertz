"""Tests for Phase 33 snippet registry."""

from __future__ import annotations

import pytest

from clawdbot.snippet_registry import (
    list_available_snippets,
    load_snippet,
    select_snippets_for_build,
    validate_snippet,
)


def test_list_snippets():
    """Verify 30+ snippets found across expected section types."""
    available = list_available_snippets()
    assert isinstance(available, dict)
    total = sum(len(v) for v in available.values())
    assert total >= 30, f"Expected 30+ snippets, found {total}"

    expected_types = ["hero", "features", "testimonials", "pricing", "cta", "contact", "faq", "navbar", "footer"]
    for st in expected_types:
        assert st in available, f"Missing section type: {st}"
        assert len(available[st]) >= 2, f"Section '{st}' has fewer than 2 variants"


def test_load_snippet():
    """Verify frontmatter parsing from a known snippet."""
    snippet = load_snippet("hero", "split-image-cta")
    assert snippet["section_type"] == "hero"
    assert snippet["variant"] == "split-image-cta"
    assert "html" in snippet
    assert len(snippet["html"]) > 100

    fm = snippet["frontmatter"]
    assert fm.get("section_type") == "hero"
    assert isinstance(fm.get("style_direction"), list)
    assert fm.get("complexity") in ("low", "medium", "high")


def test_load_snippet_not_found():
    """Loading a nonexistent snippet should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_snippet("hero", "nonexistent-variant")


def test_select_snippets():
    """Verify direction matching selects appropriate variants."""
    section_types = ["hero", "features", "testimonials", "footer"]
    result = select_snippets_for_build(
        section_types,
        direction="dark-cinematic",
        taste_weights=None,
    )
    assert isinstance(result, dict)
    for st in section_types:
        assert st in result, f"Missing selection for: {st}"
        assert isinstance(result[st], str)
        assert len(result[st]) > 0


def test_select_snippets_with_taste():
    """Taste weights should influence selection."""
    section_types = ["hero"]
    weights = {"dark-cinematic": 1.0, "minimal-geometric": 0.1}
    result = select_snippets_for_build(
        section_types,
        direction="dark-cinematic",
        taste_weights=weights,
    )
    assert "hero" in result


def test_select_snippets_missing_type():
    """Requesting a nonexistent section type should omit it from result."""
    result = select_snippets_for_build(
        ["nonexistent-section"],
        direction="minimal-geometric",
    )
    assert "nonexistent-section" not in result


def test_validate_snippet():
    """Verify CSS var detection in a real snippet."""
    snippet = load_snippet("hero", "split-image-cta")
    validation = validate_snippet(snippet["html"])
    assert validation["valid"] is True
    assert len(validation["errors"]) == 0


def test_validate_snippet_bad():
    """A snippet without CSS vars should fail validation."""
    bad_html = "<div style='color: red; background: blue;'>Hello</div>"
    validation = validate_snippet(bad_html)
    assert validation["valid"] is False
    assert len(validation["errors"]) > 0
