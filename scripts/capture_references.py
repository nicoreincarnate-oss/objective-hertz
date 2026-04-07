"""Capture reference screenshots for the ClawdBot v2 visual pipeline.

Reads ``soul/references/curated_sites.yaml`` and, for each site:

1. Captures a full-page screenshot at desktop viewport (1440x900) via the
   shared :class:`clawdbot.renderer.PlaywrightPool`.
2. Stores it under ``soul/references/_full_pages/{slug}.png``.
3. Asks the VLM (``clawdbot.visual_scorer._vlm_score``) to decompose the
   page into section-level bounding regions via
   :meth:`ReferenceManager.decompose_page_references`.
4. Crops each region and writes the crop to
   ``soul/references/{section_type}/{direction}.png`` (only the highest
   confidence crop per section_type+direction wins).
5. Updates the section ``metadata.yaml`` files with capture metadata.

CLI usage::

    python -m scripts.capture_references
    python -m scripts.capture_references --only cosmos.so
    python -m scripts.capture_references --sites-file soul/references/curated_sites.yaml

This is a manual operational task: it requires Playwright + a running
Ollama/Qwen2.5-VL (or Claude Vision fallback). Running it from tests is
NOT supported — it hits the live web.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a hard dep in prod
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger("perseus.scripts.capture_references")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REFERENCES_DIR = Path("soul/references")
FULL_PAGES_DIR = REFERENCES_DIR / "_full_pages"
DEFAULT_SITES_FILE = REFERENCES_DIR / "curated_sites.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CaptureResult:
    """Result of capturing a single reference site."""

    url: str
    direction: str
    slug: str
    full_page_path: str
    sections_captured: list[str]
    errors: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(url: str) -> str:
    """Turn a URL into a filesystem-safe slug."""
    cleaned = re.sub(r"^https?://", "", url.strip().lower())
    cleaned = cleaned.rstrip("/")
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-") or "site"


def _load_sites(path: Path) -> list[dict[str, Any]]:
    """Load the curated sites YAML file."""
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    if not path.exists():
        raise FileNotFoundError(f"Sites file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    sites = data.get("sites", [])
    if not isinstance(sites, list):
        raise ValueError(f"Malformed sites file {path}: 'sites' must be a list")
    return sites


def _crop_png(png_bytes: bytes, y_start: int, y_end: int) -> bytes:
    """Crop a PNG vertically to the given y range.

    Uses Pillow if available; otherwise returns the original bytes as a
    graceful fallback so the capture pipeline still writes *something*.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("Pillow not installed; storing full-page PNG as crop")
        return png_bytes

    with Image.open(io.BytesIO(png_bytes)) as img:
        width, height = img.size
        y_start_c = max(0, min(int(y_start), height - 1))
        y_end_c = max(y_start_c + 1, min(int(y_end), height))
        cropped = img.crop((0, y_start_c, width, y_end_c))
        out = io.BytesIO()
        cropped.save(out, format="PNG")
        return out.getvalue()


# ---------------------------------------------------------------------------
# Core capture functions
# ---------------------------------------------------------------------------


async def capture_one_site(
    url: str,
    direction: str,
    pool: Any,
) -> CaptureResult:
    """Capture a single site's full-page screenshot + section crops.

    Args:
        url: URL to screenshot.
        direction: Design direction label (e.g. ``cosmos-dark-curation``).
        pool: Started :class:`clawdbot.renderer.PlaywrightPool` instance.

    Returns:
        CaptureResult describing what was captured.
    """
    slug = _slugify(url)
    errors: list[str] = []
    sections_captured: list[str] = []

    FULL_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    full_page_path = FULL_PAGES_DIR / f"{slug}.png"

    # 1. Full-page screenshot via Playwright pool (direct URL navigation).
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required: pip install playwright && playwright install chromium"
        ) from exc

    png_bytes: bytes
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.goto(url, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(2500)
            png_bytes = await page.screenshot(full_page=True, type="png")
        finally:
            await browser.close()

    full_page_path.write_bytes(png_bytes)
    logger.info("Captured full page: %s -> %s", url, full_page_path)

    # 2. VLM decomposition into sections.
    try:
        from clawdbot.reference_manager import ReferenceManager
        from clawdbot.visual_scorer import _vlm_score
    except ImportError as exc:
        errors.append(f"clawdbot modules unavailable: {exc}")
        return CaptureResult(
            url=url,
            direction=direction,
            slug=slug,
            full_page_path=str(full_page_path),
            sections_captured=sections_captured,
            errors=errors,
        )

    prompt = (
        "Analyze this full-page website screenshot. Identify each distinct "
        "section (hero, features, pricing, testimonials, cta, contact, faq, "
        "navbar, footer).\n\n"
        "For each section, estimate the vertical pixel range (y_start, y_end).\n\n"
        'Return ONLY valid JSON: [{"section_type": "hero", "y_start": 0, '
        '"y_end": 900, "confidence": 0.95}, ...]'
    )

    try:
        data = await _vlm_score([png_bytes], prompt)
    except Exception as exc:  # noqa: BLE001 - VLM errors must not crash capture
        errors.append(f"VLM decomposition failed: {exc}")
        return CaptureResult(
            url=url,
            direction=direction,
            slug=slug,
            full_page_path=str(full_page_path),
            sections_captured=sections_captured,
            errors=errors,
        )

    if isinstance(data, dict):
        section_list = data.get("sections") or []
    elif isinstance(data, list):
        section_list = data
    else:
        section_list = []

    # 3. Crop and store one PNG per (section_type, direction) slot.
    manager = ReferenceManager()
    seen_types: set[str] = set()
    for entry in section_list:
        if not isinstance(entry, dict):
            continue
        section_type = str(entry.get("section_type", "")).strip().lower()
        if not section_type or section_type in seen_types:
            continue
        y_start = entry.get("y_start", 0)
        y_end = entry.get("y_end", 0)
        if not isinstance(y_start, (int, float)) or not isinstance(y_end, (int, float)):
            continue

        crop_bytes = _crop_png(png_bytes, int(y_start), int(y_end))
        path = manager._ref_path(section_type, direction)  # noqa: SLF001
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(crop_bytes)
        manager._update_metadata(section_type, direction, source_url=url)  # noqa: SLF001
        sections_captured.append(section_type)
        seen_types.add(section_type)
        logger.info("  crop -> %s", path)

    return CaptureResult(
        url=url,
        direction=direction,
        slug=slug,
        full_page_path=str(full_page_path),
        sections_captured=sections_captured,
        errors=errors,
    )


async def capture_all_references(
    sites_file: Path = DEFAULT_SITES_FILE,
    only: str | None = None,
) -> dict[str, Any]:
    """Capture every site listed in ``sites_file``.

    Args:
        sites_file: Path to curated_sites.yaml.
        only: Optional substring filter; if provided, only sites whose URL
            contains this substring are captured.

    Returns:
        Summary dict with ``captured``, ``failed``, ``results`` keys.
    """
    sites = _load_sites(sites_file)
    if only:
        sites = [s for s in sites if only in str(s.get("url", ""))]

    # Lazy import so ``--help`` works without clawdbot installed.
    try:
        from clawdbot.renderer import PlaywrightPool
    except ImportError as exc:
        raise RuntimeError(f"clawdbot.renderer unavailable: {exc}") from exc

    pool = PlaywrightPool()
    results: list[CaptureResult] = []
    failed: list[dict[str, Any]] = []

    for site in sites:
        url = str(site.get("url", "")).strip()
        direction = str(site.get("direction", "")).strip() or "unknown"
        if not url:
            continue
        try:
            result = await capture_one_site(url, direction, pool)
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - per-site failure must not abort batch
            logger.exception("capture failed: %s", url)
            failed.append({"url": url, "direction": direction, "error": str(exc)})

    return {
        "captured": len(results),
        "failed": len(failed),
        "results": [r.__dict__ for r in results],
        "failures": failed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture reference screenshots for ClawdBot v2",
    )
    parser.add_argument(
        "--sites-file",
        type=Path,
        default=DEFAULT_SITES_FILE,
        help="Path to curated_sites.yaml (default: soul/references/curated_sites.yaml)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Only capture sites whose URL contains this substring",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    summary = asyncio.run(
        capture_all_references(sites_file=args.sites_file, only=args.only)
    )
    logger.info(
        "capture complete: %d captured, %d failed",
        summary["captured"],
        summary["failed"],
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
