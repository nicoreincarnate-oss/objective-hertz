"""VLM-powered design taste analyzer.

Screenshots URLs via Crawl4AI and analyzes images using Qwen2.5-VL (Ollama)
or Claude Vision (fallback). Extracts structured design DNA: colors, typography,
layout, mood, style lineage.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("perseus.clawdbot.taste_analyzer")


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


class DesignDNA(BaseModel):
    colors: list[str] = Field(default_factory=list, description="Hex color values")
    color_mood: str = Field(default="", description="dark|vibrant|muted|monochrome")
    typography: str | None = Field(default=None)
    layout_style: str | None = Field(default=None)
    mood_keywords: list[str] = Field(default_factory=list)
    style_lineage: list[str] = Field(default_factory=list)
    atmosphere: list[str] = Field(default_factory=list)
    contrast: str = Field(default="medium")
    whitespace: str = Field(default="moderate")
    border_radius: str = Field(default="subtle")

    @field_validator("colors", mode="before")
    @classmethod
    def validate_hex(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [c for c in v if isinstance(c, str) and re.match(r"^#[0-9a-fA-F]{3,8}$", c)]
        return []


# ---------------------------------------------------------------------------
# VLM prompt
# ---------------------------------------------------------------------------

VLM_TASTE_PROMPT = """Analyze this design image. Extract visual DNA as JSON.

Return ONLY valid JSON matching this exact schema:
{
  "colors": ["#hex1", "#hex2", "#hex3", "#hex4"],
  "color_mood": "dark",
  "typography": "geometric-grotesque",
  "layout_style": "asymmetric",
  "mood_keywords": ["immersive", "curated", "bold"],
  "style_lineage": ["Swiss International"],
  "atmosphere": ["intellectual", "curated"],
  "contrast": "high",
  "whitespace": "generous",
  "border_radius": "sharp"
}

color_mood: dark | vibrant | muted | monochrome
typography: geometric-grotesque | humanist-sans | modern-serif | slab-serif | display-decorative | monospace | mixed
layout_style: strict-grid | asymmetric | freeform | bento | single-column | editorial
contrast: low | medium | high
whitespace: minimal | moderate | generous | extreme
border_radius: sharp | subtle | rounded | pill

Focus on the dominant visual characteristics. Be specific about colors (use hex)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_url(url: str, *, use_claude: bool = False) -> dict[str, Any]:
    """Screenshot a URL and extract its design DNA."""
    png_bytes = await _screenshot_url(url)
    return await _analyze_core(
        png_bytes,
        source=url,
        source_type="url",
        use_claude=use_claude,
    )


async def deep_analyze_url(
    url: str,
    *,
    max_pages: int = 6,
    use_claude: bool = False,
) -> dict[str, Any]:
    """Crawl multiple pages of a site and build a composite design DNA.

    Screenshots the homepage plus up to *max_pages - 1* internal links,
    analyzes each, then merges all results into one reference with higher
    confidence than a single-page analysis.
    """
    from urllib.parse import urljoin, urlparse

    screenshots: list[tuple[str, bytes]] = []

    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

        browser_cfg = BrowserConfig(headless=True, verbose=False, viewport_width=1440, viewport_height=900)
        crawl_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, screenshot=True, page_timeout=30000)

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            # 1. Homepage
            home = await crawler.arun(url=url, config=crawl_cfg)
            if home.screenshot:
                screenshots.append((url, base64.b64decode(home.screenshot)))

            # 2. Discover internal links from homepage
            internal_links: list[str] = []
            parsed_base = urlparse(url)
            for link in (home.links or {}).get("internal", []):
                href = link.get("href", "") if isinstance(link, dict) else str(link)
                if not href or href == "/" or href.startswith("#"):
                    continue
                full = urljoin(url, href)
                if urlparse(full).netloc == parsed_base.netloc and full not in internal_links:
                    internal_links.append(full)

            # 3. Screenshot up to max_pages - 1 internal pages
            for page_url in internal_links[: max_pages - 1]:
                try:
                    page = await crawler.arun(url=page_url, config=crawl_cfg)
                    if page.screenshot:
                        screenshots.append((page_url, base64.b64decode(page.screenshot)))
                        logger.info("Captured %s (%d/%d)", page_url, len(screenshots), max_pages)
                except Exception:
                    logger.debug("Failed to screenshot %s", page_url, exc_info=True)

    except Exception:
        logger.exception("Deep crawl failed for %s, falling back to single page", url)
        if not screenshots:
            png = await _screenshot_url(url)
            screenshots.append((url, png))

    if not screenshots:
        raise RuntimeError(f"No screenshots captured for {url}")

    logger.info("Deep analysis of %s: %d pages captured", url, len(screenshots))

    # 4. Analyze each screenshot
    page_dnas: list[dict[str, Any]] = []
    for page_url, png in screenshots:
        ref = await _analyze_core(png, source=page_url, source_type="url", use_claude=use_claude)
        page_dnas.append(ref)

    # 5. Merge all page analyses into one composite reference
    return _merge_page_analyses(url, page_dnas)


async def deep_analyze_artist(
    artist_name: str,
    *,
    max_images: int = 8,
    use_claude: bool = False,
) -> dict[str, Any]:
    """Search for an artist's work online and analyze multiple pieces.

    Uses DuckDuckGo image search to find representative works, downloads
    them, and runs VLM analysis on each. Merges into a composite reference.
    """
    logger.info("Deep analyzing artist: %s (max %d images)", artist_name, max_images)

    # Search for the artist's work
    image_bytes_list: list[tuple[str, bytes]] = []

    # Strategy 1: Use Crawl4AI to scrape image URLs from a gallery/search page
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

        browser_cfg = BrowserConfig(headless=True, verbose=False, viewport_width=1440, viewport_height=900)
        crawl_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=30000)

        search_url = f"https://www.google.com/search?q={artist_name.replace(' ', '+')}+graphic+design+poster&tbm=isch"
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=search_url, config=crawl_cfg)

        # Extract image URLs from the page content
        import httpx
        img_urls: list[str] = []
        # Google Images embeds data URLs and thumbnail URLs — extract full-size URLs
        for match in re.finditer(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', result.html or "", re.IGNORECASE):
            url = match.group()
            # Skip Google's own assets and tiny thumbnails
            if "google" not in url and "gstatic" not in url and "encrypted" not in url and len(url) < 500:
                if url not in img_urls:
                    img_urls.append(url)

        logger.info("Found %d candidate image URLs for %s", len(img_urls), artist_name)

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for img_url in img_urls[:max_images * 3]:
                if len(image_bytes_list) >= max_images:
                    break
                try:
                    resp = await client.get(img_url)
                    if resp.status_code == 200 and len(resp.content) > 10000:
                        image_bytes_list.append((img_url, resp.content))
                        logger.info("Downloaded image %d/%d for %s", len(image_bytes_list), max_images, artist_name)
                except Exception:
                    continue

    except Exception:
        logger.exception("Crawl4AI image search failed for %s", artist_name)

    # Strategy 2: Fallback to ddgs text search for image URLs
    if not image_bytes_list:
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                logger.warning("No search library available for artist image search")

        if "DDGS" in dir():
            try:
                with DDGS() as ddgs:  # type: ignore[possibly-undefined]
                    results = list(ddgs.text(f"{artist_name} poster art images", max_results=10))
                import httpx
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    for result in results:
                        if len(image_bytes_list) >= max_images:
                            break
                        # Try to find image links in result URLs
                        href = result.get("href", "")
                        if any(ext in href.lower() for ext in (".jpg", ".png", ".jpeg", ".webp")):
                            try:
                                resp = await client.get(href)
                                if resp.status_code == 200 and len(resp.content) > 10000:
                                    image_bytes_list.append((href, resp.content))
                            except Exception:
                                continue
            except Exception:
                logger.exception("DDG text search fallback failed for %s", artist_name)

    if not image_bytes_list:
        logger.warning("No images found for %s", artist_name)
        return _empty_reference(artist_name, "artist")

    logger.info("Analyzing %d images for %s", len(image_bytes_list), artist_name)

    # Analyze each image
    image_dnas: list[dict[str, Any]] = []
    for img_url, img_bytes in image_bytes_list:
        ref = await _analyze_core(img_bytes, source=img_url, source_type="image", use_claude=use_claude)
        image_dnas.append(ref)

    # Merge all analyses
    return _merge_page_analyses(artist_name, image_dnas)


async def analyze_image(image_path: str | Path, *, use_claude: bool = False) -> dict[str, Any]:
    """Read an image from disk and extract its design DNA."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    png_bytes = path.read_bytes()
    return await _analyze_core(
        png_bytes,
        source=str(path),
        source_type="image",
        use_claude=use_claude,
    )


async def analyze_image_bytes(
    image_bytes: bytes,
    source_label: str,
    *,
    use_claude: bool = False,
) -> dict[str, Any]:
    """Analyze raw image bytes and extract design DNA."""
    return await _analyze_core(
        image_bytes,
        source=source_label,
        source_type="bytes",
        use_claude=use_claude,
    )


async def batch_analyze(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Analyze multiple sources sequentially (rate-limit friendly).

    Each source dict: {"type": "url"|"image", "path": "...", "label": "..."}
    """
    results: list[dict[str, Any]] = []
    for src in sources:
        src_type = src.get("type", "url")
        path = src.get("path", "")
        label = src.get("label", path)
        try:
            if src_type == "url":
                result = await analyze_url(path)
            elif src_type == "image":
                result = await analyze_image(path)
            else:
                logger.warning("Unknown source type %r for %s, skipping", src_type, label)
                continue
            result["label"] = label
            results.append(result)
        except Exception:
            logger.exception("Failed to analyze source %s", label)
            results.append({"source": path, "label": label, "error": True})
    return results


async def seed_initial_taste() -> dict[str, Any]:
    """Build the initial taste profile by deeply analyzing all reference sources.

    - cosmos.so: crawls multiple pages, screenshots each
    - are.na: crawls multiple pages, screenshots each
    - Tadanori Yokoo: searches for poster art, downloads and analyzes each
    - Shigeo Fukuda: searches for poster art, downloads and analyzes each
    """
    from clawdbot.taste_profile import add_reference, load_taste_profile

    profile = load_taste_profile()

    # 1. Deep crawl reference sites (multiple pages each)
    for url in ("https://cosmos.so", "https://www.are.na"):
        try:
            logger.info("Deep crawling %s...", url)
            ref = await deep_analyze_url(url, max_pages=6)
            profile = add_reference(profile, ref)
            logger.info("Added deep reference: %s (%d pages)", url, ref.get("pages_analyzed", 1))
        except Exception:
            logger.exception("Deep crawl failed for %s, trying single page", url)
            try:
                ref = await analyze_url(url)
                profile = add_reference(profile, ref)
            except Exception:
                logger.exception("Single page also failed for %s", url)

    # 2. Deep analyze artists (search for their work, analyze many pieces)
    for artist in ("Tadanori Yokoo", "Shigeo Fukuda"):
        try:
            logger.info("Searching and analyzing %s's work...", artist)
            ref = await deep_analyze_artist(artist, max_images=8)
            if ref.get("confidence", 0) > 0:
                profile = add_reference(profile, ref)
                logger.info("Added artist reference: %s (%d images)", artist, ref.get("pages_analyzed", 0))
            else:
                logger.warning("No usable images found for %s, adding manual entry", artist)
                profile = add_reference(profile, _manual_artist_entry(artist))
        except Exception:
            logger.exception("Artist analysis failed for %s, adding manual entry", artist)
            profile = add_reference(profile, _manual_artist_entry(artist))

    return profile


def _manual_artist_entry(artist: str) -> dict[str, Any]:
    """Fallback manual entries for artists when image search fails."""
    entries = {
        "Tadanori Yokoo": {
            "source": "tadanori-yokoo-1960s-posters",
            "source_type": "manual",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "analyzer_model": "research-derived",
            "confidence": 0.9,
            "extracted": {
                "colors": ["#D42B2B", "#F5C518", "#1A1A2E", "#E8E0D5", "#FF6B35", "#8B1A8F"],
                "color_mood": "vibrant",
                "typography": "display-decorative",
                "layout_style": "freeform",
                "mood_keywords": ["psychedelic", "maximalist", "theatrical", "electric", "collage"],
                "style_lineage": ["Japanese graphic design 1960s", "Pop Art", "Psychedelia"],
                "atmosphere": ["theatrical", "electric", "sacred-profane"],
                "contrast": "high",
                "whitespace": "minimal",
                "border_radius": "sharp",
            },
        },
        "Shigeo Fukuda": {
            "source": "shigeo-fukuda-optical-illusions",
            "source_type": "manual",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "analyzer_model": "research-derived",
            "confidence": 0.9,
            "extracted": {
                "colors": ["#000000", "#FFFFFF", "#CC0000", "#1A1A1A"],
                "color_mood": "muted",
                "typography": "geometric-grotesque",
                "layout_style": "single-column",
                "mood_keywords": ["minimal", "witty", "precise", "optical-illusion", "figure-ground"],
                "style_lineage": ["Swiss International", "Japanese graphic design 1960s", "Op Art"],
                "atmosphere": ["intellectual", "precise", "witty"],
                "contrast": "high",
                "whitespace": "generous",
                "border_radius": "sharp",
            },
        },
    }
    return entries.get(artist, _empty_reference(artist, "manual"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _analyze_core(
    png_bytes: bytes,
    *,
    source: str,
    source_type: str,
    use_claude: bool,
) -> dict[str, Any]:
    """Core analysis pipeline: resize, extract colors, run VLM, merge results."""
    resized = _resize_for_vision(png_bytes)

    # Run color extraction and VLM analysis concurrently where possible.
    # Color extraction is sync so we wrap it in an executor.
    loop = asyncio.get_running_loop()
    color_future = loop.run_in_executor(None, _extract_colors, resized)
    vlm_future = _vlm_analyze(resized, use_claude=use_claude)

    colors, vlm_result = await asyncio.gather(color_future, vlm_future)

    # Parse VLM output into structured DNA
    raw_text = vlm_result.get("raw", "")
    dna = _parse_design_dna(raw_text)

    # Merge: VLM provides structure, extcolors provides accurate colors
    extracted: dict[str, Any]
    if dna is not None:
        extracted = dna.model_dump()
        # Prefer pixel-sampled colors over VLM-guessed hex values
        if colors:
            extracted["colors"] = colors
    else:
        logger.warning("VLM output unparseable for %s, using color-only fallback", source)
        extracted = DesignDNA(colors=colors).model_dump()

    return {
        "source": source,
        "source_type": source_type,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_model": vlm_result.get("model", "unknown"),
        "confidence": 0.8 if dna is not None else 0.4,
        "extracted": extracted,
    }


def _resize_for_vision(png_bytes: bytes, max_dim: int = 1024) -> bytes:
    """Resize image so its longest side is at most *max_dim* pixels.

    This keeps VLM token usage reasonable and avoids Ollama timeouts on
    very large screenshots.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(png_bytes))
        if max(img.size) <= max_dim:
            return png_bytes
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        logger.debug("Pillow not installed; skipping resize")
        return png_bytes
    except Exception:
        logger.debug("Resize failed; using original bytes", exc_info=True)
        return png_bytes


async def _screenshot_url(url: str) -> bytes:
    """Capture a full-page screenshot of *url* via Crawl4AI."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except ImportError as exc:
        raise RuntimeError(
            "crawl4ai is required for URL screenshots. Install with: pip install crawl4ai"
        ) from exc

    browser_cfg = BrowserConfig(
        headless=True,
        verbose=False,
        viewport_width=1440,
        viewport_height=900,
    )
    crawler_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        screenshot=True,
        page_timeout=30000,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=crawler_cfg)

    if result.screenshot:
        return base64.b64decode(result.screenshot)
    raise RuntimeError(f"No screenshot captured for {url}")


def _extract_colors(png_bytes: bytes) -> list[str]:
    """Extract dominant colors from image bytes.

    Uses extcolors (primary) with colorthief as fallback. Returns up to
    8 hex strings sorted by pixel frequency.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(png_bytes))
    except ImportError:
        logger.warning("Pillow not installed; color extraction unavailable")
        return []
    except Exception:
        logger.warning("Failed to open image for color extraction", exc_info=True)
        return []

    # Primary: extcolors (frequency-sorted)
    try:
        import extcolors

        colors, _ = extcolors.extract_from_image(img, tolerance=32, limit=8)
        return [f"#{r:02x}{g:02x}{b:02x}" for (r, g, b), _ in colors]
    except ImportError:
        logger.debug("extcolors not installed; trying colorthief")
    except Exception:
        logger.debug("extcolors extraction failed; trying colorthief", exc_info=True)

    # Fallback: colorthief
    try:
        from colorthief import ColorThief

        ct = ColorThief(BytesIO(png_bytes))
        palette = ct.get_palette(color_count=6)
        return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
    except ImportError:
        logger.warning("Neither extcolors nor colorthief installed; no color data")
    except Exception:
        logger.warning("colorthief extraction failed", exc_info=True)

    return []


async def _vlm_analyze(png_bytes: bytes, *, use_claude: bool = False) -> dict[str, Any]:
    """Run VLM analysis on image bytes.

    Default: Ollama with Qwen2.5-VL. Fallback/override: Claude Vision.
    """
    if use_claude:
        return await _vlm_claude(png_bytes)

    # Try Ollama first, fall back to Claude on failure
    try:
        return await _vlm_ollama(png_bytes)
    except Exception:
        logger.warning("Ollama VLM failed; falling back to Claude Vision", exc_info=True)
        try:
            return await _vlm_claude(png_bytes)
        except Exception:
            logger.error("Both Ollama and Claude VLM failed", exc_info=True)
            return {"raw": "", "model": "none"}


async def _vlm_ollama(png_bytes: bytes) -> dict[str, Any]:
    """Analyze via Ollama vision model (Qwen2.5-VL)."""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for Ollama VLM calls") from exc

    from shared.config import config

    img_b64 = base64.b64encode(png_bytes).decode()
    model = getattr(config.ollama, "vision_model", "qwen2.5vl:7b")
    host = config.ollama.host

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": VLM_TASTE_PROMPT,
                        "images": [img_b64],
                    }
                ],
                "stream": False,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = data.get("message", {}).get("content", "")
    return {"raw": content, "model": model}


async def _vlm_claude(png_bytes: bytes) -> dict[str, Any]:
    """Analyze via Claude Vision (shared LLM client)."""
    from shared.llm_client import llm

    result = await llm.generate_with_images(
        VLM_TASTE_PROMPT,
        images=[png_bytes],
        model="smart",
        max_tokens=800,
        temperature=0.1,
    )
    return {"raw": result, "model": "claude-vision"}


def _parse_design_dna(raw_text: str) -> DesignDNA | None:
    """Parse VLM output into a validated DesignDNA, or None on failure."""
    if not raw_text:
        return None

    # Strip markdown code fences if present
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1 :]
        # Remove closing fence
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3].rstrip()

    # Try direct JSON parse
    try:
        data = json.loads(cleaned)
        return DesignDNA.model_validate(data)
    except (json.JSONDecodeError, Exception):
        pass

    # Try extracting first JSON object from the text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw_text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return DesignDNA.model_validate(data)
        except (json.JSONDecodeError, Exception):
            pass

    logger.debug("Could not parse design DNA from VLM output: %s", raw_text[:200])
    return None


def _merge_page_analyses(source_label: str, analyses: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple page/image analyses into one composite reference.

    Aggregates colors by frequency, takes mode of categorical fields,
    unions all keywords/lineage/atmosphere.
    """
    from collections import Counter

    all_colors: list[str] = []
    all_moods: list[str] = []
    all_lineage: list[str] = []
    all_atmosphere: list[str] = []
    color_moods: list[str] = []
    typographies: list[str] = []
    layouts: list[str] = []
    contrasts: list[str] = []
    whitespaces: list[str] = []
    border_radii: list[str] = []
    models: list[str] = []

    for ref in analyses:
        ext = ref.get("extracted", {})
        all_colors.extend(ext.get("colors", []))
        all_moods.extend(ext.get("mood_keywords", []))
        all_lineage.extend(ext.get("style_lineage", []))
        all_atmosphere.extend(ext.get("atmosphere", []))
        if ext.get("color_mood"):
            color_moods.append(ext["color_mood"])
        if ext.get("typography"):
            typographies.append(ext["typography"])
        if ext.get("layout_style"):
            layouts.append(ext["layout_style"])
        if ext.get("contrast"):
            contrasts.append(ext["contrast"])
        if ext.get("whitespace"):
            whitespaces.append(ext["whitespace"])
        if ext.get("border_radius"):
            border_radii.append(ext["border_radius"])
        models.append(ref.get("analyzer_model", "unknown"))

    def mode(vals: list[str]) -> str:
        return Counter(vals).most_common(1)[0][0] if vals else ""

    # Top colors by frequency, deduplicated
    color_counts = Counter(all_colors)
    top_colors = [c for c, _ in color_counts.most_common(8)]

    # Mood keywords ranked by frequency
    mood_counts = Counter(all_moods)
    top_moods = [m for m, _ in mood_counts.most_common(10)]

    return {
        "source": source_label,
        "source_type": "deep_analysis",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_model": mode(models),
        "confidence": min(0.95, 0.6 + 0.05 * len(analyses)),
        "pages_analyzed": len(analyses),
        "extracted": {
            "colors": top_colors,
            "color_mood": mode(color_moods),
            "typography": mode(typographies),
            "layout_style": mode(layouts),
            "mood_keywords": top_moods,
            "style_lineage": list(dict.fromkeys(all_lineage)),
            "atmosphere": list(dict.fromkeys(all_atmosphere)),
            "contrast": mode(contrasts),
            "whitespace": mode(whitespaces),
            "border_radius": mode(border_radii),
        },
    }


def _empty_reference(source: str, source_type: str) -> dict[str, Any]:
    """Return an empty reference when analysis completely fails."""
    return {
        "source": source,
        "source_type": source_type,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_model": "none",
        "confidence": 0.0,
        "extracted": DesignDNA().model_dump(),
    }
