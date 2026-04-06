"""Snippet loader and selector for the ClawdBot component library.

Reads HTML snippet files from soul/components/, parses YAML frontmatter,
and selects the best variant per section type based on design direction
and taste profile weights.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("perseus.clawdbot.snippet_registry")

SNIPPETS_DIR = Path(__file__).resolve().parent.parent / "soul" / "components"

# CSS custom property names that every snippet should use instead of hardcoded values
_REQUIRED_CSS_VARS = [
    "var(--color-",
    "var(--font-display)",
    "var(--font-body)",
]

_FORBIDDEN_HARDCODED = re.compile(
    r'(?:style=["\'][^"\']*)'
    r'(?:color:\s*#[0-9a-fA-F]{3,8}'
    r"|background(?:-color)?:\s*#[0-9a-fA-F]{3,8})"
)


def load_snippet(section_type: str, variant: str) -> dict[str, Any]:
    """Read an HTML snippet file and parse its YAML frontmatter.

    Returns a dict with keys: section_type, variant, frontmatter (dict),
    html (str), path (str).

    Raises FileNotFoundError if the snippet does not exist.
    """
    snippet_path = SNIPPETS_DIR / section_type / f"{variant}.html"
    if not snippet_path.exists():
        raise FileNotFoundError(f"Snippet not found: {snippet_path}")

    raw = snippet_path.read_text(encoding="utf-8")
    frontmatter, html = _parse_frontmatter(raw)

    return {
        "section_type": section_type,
        "variant": variant,
        "frontmatter": frontmatter,
        "html": html.strip(),
        "path": str(snippet_path),
    }


def list_available_snippets() -> dict[str, list[str]]:
    """Return a mapping of section_type -> list of variant names.

    Scans soul/components/ for subdirectories containing .html files.
    """
    result: dict[str, list[str]] = {}
    if not SNIPPETS_DIR.exists():
        return result

    for section_dir in sorted(SNIPPETS_DIR.iterdir()):
        if not section_dir.is_dir():
            continue
        section_type = section_dir.name
        variants = sorted(
            p.stem for p in section_dir.glob("*.html") if p.is_file()
        )
        if variants:
            result[section_type] = variants

    return result


def select_snippets_for_build(
    section_types: list[str],
    direction: str,
    taste_weights: dict[str, float] | None = None,
) -> dict[str, str]:
    """Pick the best snippet variant for each requested section type.

    Selection criteria (in priority order):
    1. Direction match: snippet frontmatter style_direction includes the direction
    2. Taste weight: if taste_weights provided, prefer snippets whose directions
       have higher taste scores
    3. Complexity: prefer medium over high for demo tier

    Returns {section_type: variant_name} for each requested section.
    Missing section types are omitted from the result.
    """
    available = list_available_snippets()
    result: dict[str, str] = {}

    for section_type in section_types:
        variants = available.get(section_type, [])
        if not variants:
            logger.warning("No snippets available for section type: %s", section_type)
            continue

        best_variant = variants[0]
        best_score = -1.0

        for variant_name in variants:
            try:
                snippet = load_snippet(section_type, variant_name)
            except (FileNotFoundError, ValueError):
                continue

            fm = snippet.get("frontmatter", {})
            style_dirs = fm.get("style_direction", [])
            if isinstance(style_dirs, str):
                style_dirs = [style_dirs]

            score = 0.0

            # Direction match bonus
            if direction in style_dirs:
                score += 10.0

            # Taste weight bonus
            if taste_weights:
                for sd in style_dirs:
                    score += taste_weights.get(sd, 0.0)

            # Complexity preference (medium preferred over high)
            complexity = fm.get("complexity", "medium")
            if complexity == "low":
                score += 1.0
            elif complexity == "medium":
                score += 0.5

            if score > best_score:
                best_score = score
                best_variant = variant_name

        result[section_type] = best_variant

    return result


def validate_snippet(html: str) -> dict[str, Any]:
    """Validate that an HTML snippet follows component library conventions.

    Checks:
    - Uses CSS custom properties for colors and fonts
    - Contains a <section> or <nav> or <footer> root tag
    - No obviously hardcoded hex colors in inline styles

    Returns a dict with 'valid' (bool), 'errors' (list[str]),
    'warnings' (list[str]).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check for CSS custom properties
    has_css_vars = any(v in html for v in _REQUIRED_CSS_VARS)
    if not has_css_vars:
        errors.append("Snippet does not use CSS custom properties (var(--color-*), var(--font-*))")

    # Check for root element
    has_root = bool(re.search(r"<(?:section|nav|footer|header)\b", html, re.IGNORECASE))
    if not has_root:
        warnings.append("Snippet does not have a <section>, <nav>, <footer>, or <header> root element")

    # Check for hardcoded colors in inline styles
    hardcoded_matches = _FORBIDDEN_HARDCODED.findall(html)
    if hardcoded_matches:
        warnings.append(
            f"Found {len(hardcoded_matches)} potentially hardcoded color(s) in inline styles"
        )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a file into YAML frontmatter dict and remaining HTML content."""
    if not raw.startswith("---"):
        return {}, raw

    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw

    yaml_str = parts[1].strip()
    html = parts[2]

    try:
        frontmatter = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        logger.warning("Failed to parse YAML frontmatter")
        frontmatter = {}

    return frontmatter, html
