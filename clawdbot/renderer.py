"""Playwright rendering service for ClawdBot site generation.

Renders HTML strings to PNG screenshots via headless Chromium.
Provides a browser pool for efficient multi-section rendering
and section-specific rendering with design tokens.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clawdbot.design_tokens import DesignTokens

try:
    from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
except ImportError:
    async_playwright = None  # type: ignore[assignment,misc]
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Playwright = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default viewport presets
# ---------------------------------------------------------------------------

VIEWPORT_DESKTOP = {"width": 1440, "height": 900}
VIEWPORT_TABLET = {"width": 768, "height": 1024}
VIEWPORT_MOBILE = {"width": 375, "height": 812}

DEFAULT_VIEWPORTS: dict[str, dict[str, int]] = {
    "desktop": VIEWPORT_DESKTOP,
    "tablet": VIEWPORT_TABLET,
    "mobile": VIEWPORT_MOBILE,
}


# ---------------------------------------------------------------------------
# Standalone render function
# ---------------------------------------------------------------------------


async def render_html_to_png(
    html: str,
    viewport: dict[str, int] | None = None,
    wait_ms: int = 2000,
    full_page: bool = False,
) -> bytes:
    """Render HTML string to PNG via Playwright headless Chromium.

    Args:
        html: Full HTML document string.
        viewport: Browser viewport size dict with ``width`` and ``height``.
        wait_ms: Milliseconds to wait after page load for JS/animations.
        full_page: If True, capture full scroll height.

    Returns:
        PNG image bytes.
    """
    if async_playwright is None:
        raise RuntimeError("playwright is required: pip install playwright && playwright install chromium")

    vp = viewport or VIEWPORT_DESKTOP

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=vp)
            await page.set_content(html, wait_until="load")
            await page.wait_for_timeout(wait_ms)
            png_bytes: bytes = await page.screenshot(full_page=full_page, type="png")
            return png_bytes
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# PlaywrightPool — pre-warmed browser pool
# ---------------------------------------------------------------------------


class PlaywrightPool:
    """Pre-warmed browser pool for fast section rendering.

    Pre-creates multiple browser contexts at startup so that individual
    render calls don't pay the browser-launch cost.  Thread-safe via
    ``asyncio.Semaphore``.
    """

    def __init__(self) -> None:
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._contexts: list[Any] = []
        self._semaphore: asyncio.Semaphore | None = None
        self._context_queue: asyncio.Queue[Any] | None = None
        self._started = False

    # -- lifecycle -----------------------------------------------------------

    async def start(self, pool_size: int = 3) -> None:
        """Pre-warm *pool_size* browser contexts."""
        if async_playwright is None:
            raise RuntimeError("playwright is required: pip install playwright && playwright install chromium")

        if self._started:
            return

        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._semaphore = asyncio.Semaphore(pool_size)
        self._context_queue = asyncio.Queue()

        for _ in range(pool_size):
            ctx = await self._browser.new_context()
            self._contexts.append(ctx)
            await self._context_queue.put(ctx)

        self._started = True
        logger.info("PlaywrightPool started with %d contexts", pool_size)

    async def shutdown(self) -> None:
        """Close all contexts and the browser."""
        if not self._started:
            return

        for ctx in self._contexts:
            try:
                await ctx.close()
            except Exception:
                logger.debug("Error closing browser context", exc_info=True)

        self._contexts.clear()

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Error closing browser", exc_info=True)

        if self._pw:
            try:
                await self._pw.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error stopping playwright", exc_info=True)

        self._started = False
        logger.info("PlaywrightPool shut down")

    # -- rendering -----------------------------------------------------------

    async def render(
        self,
        html: str,
        viewport: dict[str, int] | None = None,
        wait_ms: int = 2000,
    ) -> bytes:
        """Render HTML to PNG using a pooled browser context.

        Args:
            html: Full HTML document string.
            viewport: Optional viewport override.
            wait_ms: Milliseconds to wait after load.

        Returns:
            PNG bytes.
        """
        if not self._started:
            raise RuntimeError("PlaywrightPool not started — call start() first")

        assert self._semaphore is not None
        assert self._context_queue is not None

        vp = viewport or VIEWPORT_DESKTOP

        async with self._semaphore:
            ctx = await self._context_queue.get()
            try:
                page = await ctx.new_page(viewport=vp)
                try:
                    await page.set_content(html, wait_until="load")
                    await page.wait_for_timeout(wait_ms)
                    png_bytes: bytes = await page.screenshot(type="png")
                    return png_bytes
                finally:
                    await page.close()
            finally:
                await self._context_queue.put(ctx)

    async def render_multi_viewport(
        self,
        html: str,
        wait_ms: int = 2000,
    ) -> dict[str, bytes]:
        """Render HTML at desktop, tablet, and mobile viewports.

        Returns:
            Dict mapping viewport name to PNG bytes.
        """
        results: dict[str, bytes] = {}
        for name, vp in DEFAULT_VIEWPORTS.items():
            results[name] = await self.render(html, viewport=vp, wait_ms=wait_ms)
        return results


# ---------------------------------------------------------------------------
# Section-in-page rendering
# ---------------------------------------------------------------------------


def _build_page_shell(section_html: str, design_tokens: DesignTokens) -> str:
    """Wrap a section fragment in a full HTML page with design tokens CSS."""
    from clawdbot.design_tokens import build_head_block, tokens_to_css_vars

    css_vars = tokens_to_css_vars(design_tokens)
    head_block = build_head_block(design_tokens)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {head_block}
    <style>
        :root {{
            {css_vars}
        }}
        *, *::before, *::after {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: var(--font-body), system-ui, sans-serif;
            background: var(--color-background);
            color: var(--color-text-primary);
            line-height: var(--body-line-height, 1.6);
        }}
    </style>
</head>
<body>
    {section_html}
</body>
</html>"""


async def render_section_in_page(
    section_html: str,
    design_tokens: DesignTokens,
    viewport: dict[str, int] | None = None,
    wait_ms: int = 2000,
    pool: PlaywrightPool | None = None,
) -> bytes:
    """Wrap a section fragment in a page shell with design tokens and screenshot it.

    If a ``PlaywrightPool`` is provided, uses it for rendering; otherwise
    falls back to the standalone ``render_html_to_png``.

    Args:
        section_html: HTML fragment for the section.
        design_tokens: DesignTokens driving CSS custom properties.
        viewport: Optional viewport override.
        wait_ms: Milliseconds to wait after load.
        pool: Optional PlaywrightPool for efficient pooled rendering.

    Returns:
        PNG bytes of the rendered section.
    """
    full_html = _build_page_shell(section_html, design_tokens)

    if pool is not None:
        return await pool.render(full_html, viewport=viewport, wait_ms=wait_ms)

    return await render_html_to_png(full_html, viewport=viewport, wait_ms=wait_ms)
