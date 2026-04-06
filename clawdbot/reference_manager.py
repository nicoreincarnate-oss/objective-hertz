"""Reference screenshot manager for ClawdBot visual comparison.

Manages a library of curated reference screenshots organized by
section type and design direction.  Used by ``visual_scorer.compare_to_reference``
to evaluate generated sections against known-good aesthetics.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class ReferenceManager:
    """Manages reference screenshots for section comparison.

    Storage layout::

        soul/references/
        +-- hero/
        |   +-- cosmos-dark-curation.png
        |   +-- minimal-geometric.png
        |   +-- metadata.yaml
        +-- features/
        |   +-- cosmos-dark-curation.png
        |   +-- metadata.yaml
        +-- ...
    """

    REFERENCES_DIR = Path("soul/references/")

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or self.REFERENCES_DIR

    # -- retrieval -----------------------------------------------------------

    async def get_section_reference(
        self,
        section_type: str,
        direction: str,
    ) -> bytes | None:
        """Get a reference screenshot for the given section type + direction.

        Args:
            section_type: E.g. "hero", "features", "pricing".
            direction: E.g. "cosmos-dark-curation", "minimal-geometric".

        Returns:
            PNG bytes if reference exists, else None.
        """
        path = self._ref_path(section_type, direction)
        if not path.exists():
            logger.debug("No reference: %s/%s", section_type, direction)
            return None
        return path.read_bytes()

    # -- capture -------------------------------------------------------------

    async def capture_reference(
        self,
        url: str,
        section_type: str,
        direction: str,
    ) -> None:
        """Screenshot a URL and store as a reference image.

        Args:
            url: URL to screenshot.
            section_type: Section type label.
            direction: Design direction label.
        """
        if async_playwright is None:
            raise RuntimeError("playwright is required for reference capture")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                png_bytes = await page.screenshot(full_page=False, type="png")
            finally:
                await browser.close()

        path = self._ref_path(section_type, direction)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes)
        self._update_metadata(section_type, direction, source_url=url)
        logger.info("Captured reference: %s", path)

    # -- listing -------------------------------------------------------------

    async def list_references(self) -> dict[str, list[str]]:
        """List all available references grouped by section type.

        Returns:
            Dict mapping section type to list of direction names.
        """
        result: dict[str, list[str]] = {}
        if not self._base.exists():
            return result

        for section_dir in sorted(self._base.iterdir()):
            if not section_dir.is_dir():
                continue
            directions = [
                p.stem
                for p in sorted(section_dir.glob("*.png"))
            ]
            if directions:
                result[section_dir.name] = directions
        return result

    # -- decomposition -------------------------------------------------------

    async def decompose_page_references(
        self,
        url: str,
    ) -> list[dict[str, Any]]:
        """VLM-analyze a full page screenshot to identify section crops.

        Captures a full-page screenshot and uses the VLM to identify
        distinct sections (hero, features, pricing, etc.) with their
        approximate bounding regions.

        Args:
            url: URL of the page to decompose.

        Returns:
            List of dicts with ``section_type``, ``y_start``, ``y_end``,
            ``confidence`` keys.
        """
        if async_playwright is None:
            raise RuntimeError("playwright is required for page decomposition")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                png_bytes = await page.screenshot(full_page=True, type="png")
            finally:
                await browser.close()

        from clawdbot.visual_scorer import _vlm_score

        prompt = (
            "Analyze this full-page website screenshot. Identify each distinct "
            "section (hero, features, pricing, testimonials, CTA, footer, etc.).\n\n"
            "For each section, estimate the approximate vertical pixel range.\n\n"
            'Return ONLY valid JSON: [{"section_type": "hero", "y_start": 0, '
            '"y_end": 900, "confidence": 0.95}, ...]'
        )

        data = await _vlm_score([png_bytes], prompt)

        # data may be a dict with a list inside, or a list directly
        if isinstance(data, dict):
            sections = data.get("sections", [])
            if not sections and isinstance(data.get("raw"), str):
                sections = []
        elif isinstance(data, list):
            sections = data
        else:
            sections = []

        return sections

    # -- internals -----------------------------------------------------------

    def _ref_path(self, section_type: str, direction: str) -> Path:
        """Return the filesystem path for a reference screenshot."""
        safe_section = section_type.lower().replace(" ", "-")
        safe_direction = direction.lower().replace(" ", "-")
        return self._base / safe_section / f"{safe_direction}.png"

    def _update_metadata(
        self,
        section_type: str,
        direction: str,
        *,
        source_url: str = "",
    ) -> None:
        """Update metadata.yaml in the section directory."""
        section_dir = self._base / section_type.lower().replace(" ", "-")
        meta_path = section_dir / "metadata.yaml"

        meta: dict[str, Any] = {}
        if yaml is not None and meta_path.exists():
            try:
                meta = yaml.safe_load(meta_path.read_text()) or {}
            except (yaml.YAMLError, OSError):
                meta = {}

        safe_direction = direction.lower().replace(" ", "-")
        meta.setdefault("references", {})[safe_direction] = {
            "source_url": source_url,
            "file": f"{safe_direction}.png",
        }

        if yaml is not None:
            meta_path.write_text(yaml.dump(meta, default_flow_style=False))
        else:
            # Fallback: write simple text
            lines = [f"# {section_type} references\n"]
            for ref_name, ref_data in meta.get("references", {}).items():
                lines.append(f"{ref_name}: {ref_data.get('source_url', '')}\n")
            meta_path.write_text("".join(lines))
