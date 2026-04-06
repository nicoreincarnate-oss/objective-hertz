"""Tests for clawdbot.renderer — Playwright rendering service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawdbot.renderer import (
    PlaywrightPool,
    _build_page_shell,
    render_html_to_png,
    render_section_in_page,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-png-data"


def _mock_playwright_context():
    """Build a mock async_playwright context manager chain."""
    page = AsyncMock()
    page.set_content = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock(return_value=FAKE_PNG)
    page.close = AsyncMock()
    page.goto = AsyncMock()

    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.close = AsyncMock()

    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    browser.new_context = AsyncMock(return_value=ctx)
    browser.close = AsyncMock()

    chromium = AsyncMock()
    chromium.launch = AsyncMock(return_value=browser)

    pw_instance = AsyncMock()
    pw_instance.chromium = chromium

    pw_cm = AsyncMock()
    pw_cm.__aenter__ = AsyncMock(return_value=pw_instance)
    pw_cm.__aexit__ = AsyncMock(return_value=None)

    return pw_cm, pw_instance, browser, ctx, page


# ---------------------------------------------------------------------------
# render_html_to_png
# ---------------------------------------------------------------------------


class TestRenderHtmlToPng:
    @pytest.mark.asyncio
    async def test_basic_render(self):
        pw_cm, _, browser, _, page = _mock_playwright_context()

        with patch("clawdbot.renderer.async_playwright", return_value=pw_cm):
            result = await render_html_to_png("<h1>Hello</h1>")

        assert result == FAKE_PNG
        page.set_content.assert_awaited_once()
        page.wait_for_timeout.assert_awaited_once_with(2000)
        page.screenshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_custom_viewport(self):
        pw_cm, _, browser, _, page = _mock_playwright_context()

        with patch("clawdbot.renderer.async_playwright", return_value=pw_cm):
            await render_html_to_png("<p>test</p>", viewport={"width": 375, "height": 812})

        browser.new_page.assert_awaited_once()
        call_kwargs = browser.new_page.call_args[1]
        assert call_kwargs["viewport"] == {"width": 375, "height": 812}

    @pytest.mark.asyncio
    async def test_full_page_screenshot(self):
        pw_cm, _, browser, _, page = _mock_playwright_context()

        with patch("clawdbot.renderer.async_playwright", return_value=pw_cm):
            await render_html_to_png("<p>test</p>", full_page=True)

        page.screenshot.assert_awaited_once_with(full_page=True, type="png")

    @pytest.mark.asyncio
    async def test_playwright_missing_raises(self):
        with patch("clawdbot.renderer.async_playwright", None):
            with pytest.raises(RuntimeError, match="playwright is required"):
                await render_html_to_png("<p>test</p>")


# ---------------------------------------------------------------------------
# PlaywrightPool
# ---------------------------------------------------------------------------


class TestPlaywrightPool:
    @pytest.mark.asyncio
    async def test_start_creates_contexts(self):
        pw_cm, pw_inst, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()

        # Mock async_playwright to return our context manager
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=3)

        assert pool._started
        assert browser.new_context.await_count == 3
        assert len(pool._contexts) == 3

    @pytest.mark.asyncio
    async def test_render_uses_pooled_context(self):
        pw_cm, pw_inst, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=1)
            result = await pool.render("<h1>test</h1>")

        assert result == FAKE_PNG
        ctx.new_page.assert_awaited()
        page.set_content.assert_awaited()
        page.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_render_not_started_raises(self):
        pool = PlaywrightPool()
        with pytest.raises(RuntimeError, match="not started"):
            await pool.render("<p>test</p>")

    @pytest.mark.asyncio
    async def test_shutdown(self):
        pw_cm, pw_inst, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=2)
            await pool.shutdown()

        assert not pool._started
        assert len(pool._contexts) == 0
        assert browser.close.await_count == 1

    @pytest.mark.asyncio
    async def test_multi_viewport_returns_three(self):
        pw_cm, pw_inst, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=3)
            results = await pool.render_multi_viewport("<h1>test</h1>")

        assert set(results.keys()) == {"desktop", "tablet", "mobile"}
        for _name, png in results.items():
            assert png == FAKE_PNG

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        pw_cm, pw_inst, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=2)
            await pool.start(pool_size=5)  # should be no-op

        # Only 2 contexts created (first start)
        assert browser.new_context.await_count == 2


# ---------------------------------------------------------------------------
# _build_page_shell / render_section_in_page
# ---------------------------------------------------------------------------


class TestSectionRendering:
    def test_build_page_shell_includes_tokens(self):
        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens(
            primary="#ff0000",
            accent="#00ff00",
            font_display="Inter",
            font_body="DM Sans",
        )

        html = _build_page_shell("<section>Hello</section>", tokens)

        assert "<!DOCTYPE html>" in html
        assert "<section>Hello</section>" in html
        assert "var(--font-body)" in html
        assert "var(--color-background)" in html

    @pytest.mark.asyncio
    async def test_render_section_in_page_standalone(self):
        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()
        pw_cm, _, browser, _, page = _mock_playwright_context()

        with patch("clawdbot.renderer.async_playwright", return_value=pw_cm):
            result = await render_section_in_page(
                "<div>Test section</div>",
                tokens,
            )

        assert result == FAKE_PNG

    @pytest.mark.asyncio
    async def test_render_section_in_page_with_pool(self):
        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()
        pw_cm, _, browser, ctx, page = _mock_playwright_context()

        pool = PlaywrightPool()
        mock_ap = MagicMock(return_value=pw_cm)

        with patch("clawdbot.renderer.async_playwright", mock_ap):
            await pool.start(pool_size=1)
            result = await render_section_in_page(
                "<div>Test section</div>",
                tokens,
                pool=pool,
            )

        assert result == FAKE_PNG
