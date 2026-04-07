"""Unit tests for clawdbot.snippet_validator.

Phase 33-38 modules (snippet_registry, visual_scorer, renderer) are
stubbed via sys.modules so these tests run in isolation without the
real dependencies.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class _FakeSectionScore:
    overall: float = 0.0
    hierarchy: float = 0.0
    spacing: float = 0.0
    typography: float = 0.0
    color_usage: float = 0.0
    component_quality: float = 0.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = False


def _install_stub_modules(
    *,
    snippets: dict[str, dict[str, str]],
    score_by_type: dict[str, float],
    render_raises: set[str] | None = None,
) -> None:
    """Install fake clawdbot.snippet_registry / visual_scorer / renderer.

    Args:
        snippets: mapping of section_type -> {variant: html}
        score_by_type: mapping of section_type -> overall score returned
            by the fake scorer
        render_raises: set of variant names whose render should raise
    """
    render_raises = render_raises or set()

    reg = types.ModuleType("clawdbot.snippet_registry")

    def list_available_snippets() -> dict[str, list[str]]:
        return {st: list(variants) for st, variants in snippets.items()}

    def load_snippet(section_type: str, variant: str) -> dict[str, Any]:
        html = snippets[section_type][variant]
        return {
            "section_type": section_type,
            "variant": variant,
            "frontmatter": {},
            "html": html,
            "path": f"soul/components/{section_type}/{variant}.html",
        }

    reg.list_available_snippets = list_available_snippets  # type: ignore[attr-defined]
    reg.load_snippet = load_snippet  # type: ignore[attr-defined]
    sys.modules["clawdbot.snippet_registry"] = reg

    scorer_mod = types.ModuleType("clawdbot.visual_scorer")

    async def score_section_quality(
        png_bytes: bytes, section_type: str
    ) -> _FakeSectionScore:
        return _FakeSectionScore(overall=score_by_type.get(section_type, 7.0))

    scorer_mod.score_section_quality = score_section_quality  # type: ignore[attr-defined]
    sys.modules["clawdbot.visual_scorer"] = scorer_mod

    renderer_mod = types.ModuleType("clawdbot.renderer")

    async def render_html_to_png(
        html: str, viewport: dict | None = None, wait_ms: int = 2000, full_page: bool = False
    ) -> bytes:
        for variant in render_raises:
            if variant in html:
                raise RuntimeError(f"playwright failed for {variant}")
        return b"\x89PNG-fake-" + html[:20].encode("utf-8", errors="ignore")

    renderer_mod.render_html_to_png = render_html_to_png  # type: ignore[attr-defined]

    class PlaywrightPool:  # minimal placeholder
        async def render(self, html: str) -> bytes:
            return await render_html_to_png(html)

    renderer_mod.PlaywrightPool = PlaywrightPool  # type: ignore[attr-defined]
    sys.modules["clawdbot.renderer"] = renderer_mod


@pytest.fixture(autouse=True)
def _cleanup_stubs():
    """Remove stub modules after each test."""
    yield
    for name in (
        "clawdbot.snippet_registry",
        "clawdbot.visual_scorer",
        "clawdbot.renderer",
    ):
        sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_validate_all_snippets_clean() -> None:
    """Snippets with CSS vars + good scores all pass."""
    _install_stub_modules(
        snippets={
            "hero": {
                "split": '<section class="bg-[var(--color-bg)]" style="font-family: var(--font-display);">Hero</section>',
            },
            "features": {
                "grid": '<section style="color: var(--color-fg);">Features</section>',
            },
        },
        score_by_type={"hero": 8.5, "features": 9.0},
    )
    from clawdbot.snippet_validator import validate_all_snippets

    report = await validate_all_snippets()

    assert report.total == 2
    assert report.passed == 2
    assert report.failed == 0
    assert report.broken_renders == []
    assert report.low_scoring == []


@pytest.mark.asyncio
async def test_broken_render_detection() -> None:
    """Snippets whose render raises are flagged broken."""
    _install_stub_modules(
        snippets={
            "hero": {
                "broken": '<section style="color: var(--color-fg);">Broken hero marker</section>',
            },
        },
        score_by_type={"hero": 8.0},
        render_raises={"Broken hero marker"},
    )
    from clawdbot.snippet_validator import validate_all_snippets

    report = await validate_all_snippets()

    assert report.total == 1
    assert report.failed == 1
    assert len(report.broken_renders) == 1
    finding = report.findings[0]
    assert "broken_render" in finding.flags
    assert finding.error is not None


@pytest.mark.asyncio
async def test_low_score_detection() -> None:
    """Snippets with VLM overall < 6.0 are flagged low_score."""
    _install_stub_modules(
        snippets={
            "hero": {
                "weak": '<section style="color: var(--color-fg);">Hero</section>',
            },
        },
        score_by_type={"hero": 4.2},
    )
    from clawdbot.snippet_validator import validate_all_snippets

    report = await validate_all_snippets()

    assert report.total == 1
    assert report.failed == 1
    assert len(report.low_scoring) == 1
    assert report.low_scoring[0]["score"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_missing_css_vars_flag() -> None:
    """Snippets without var(--color-*) are flagged."""
    _install_stub_modules(
        snippets={
            "hero": {
                "hardcoded": "<section>Hero with #fff hardcoded</section>",
            },
        },
        score_by_type={"hero": 8.0},
    )
    from clawdbot.snippet_validator import validate_all_snippets

    report = await validate_all_snippets()

    assert "missing_css_vars" in report.findings[0].flags
    assert len(report.missing_css_vars) == 1


@pytest.mark.asyncio
async def test_placeholder_detection() -> None:
    """Lorem ipsum / TODO markers flagged."""
    _install_stub_modules(
        snippets={
            "hero": {
                "loremy": '<section style="color: var(--color-fg);">Lorem ipsum dolor sit amet TODO</section>',
            },
        },
        score_by_type={"hero": 8.0},
    )
    from clawdbot.snippet_validator import validate_all_snippets

    report = await validate_all_snippets()

    assert "placeholder_text" in report.findings[0].flags
    assert len(report.placeholders) == 1
