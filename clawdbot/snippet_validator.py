"""Monthly validation pipeline for the ClawdBot component snippet library.

The snippet registry (``soul/components/``) is a curated set of HTML
fragments that section agents reach for. Over time CDNs rev, Tailwind
classes get deprecated, and visual quality can drift. This module
renders every snippet with neutral design tokens and VLM-scores the
result, flagging:

* broken renders (Playwright raises or returns empty bytes)
* low VLM scores (< 6.0 overall — visual quality degraded)
* missing CSS custom property references (``var(--color-*)`` etc.)
* placeholder patterns ("Lorem ipsum", "TODO", "PLACEHOLDER")

Run manually or as a Perseus scheduled task::

    python -m clawdbot.snippet_validator
    python -m clawdbot.snippet_validator --section-type hero
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clawdbot.renderer import PlaywrightPool

logger = logging.getLogger("perseus.clawdbot.snippet_validator")

# ---------------------------------------------------------------------------
# Thresholds and patterns
# ---------------------------------------------------------------------------

LOW_SCORE_THRESHOLD = 6.0
REQUIRED_CSS_VARS = (
    "var(--color-",
    "var(--font-display)",
    "var(--font-body)",
)
PLACEHOLDER_PATTERNS = re.compile(
    r"\b(lorem ipsum|lorem\s+ipsum|placeholder|todo|fixme|xxx|tbd)\b",
    re.IGNORECASE,
)
DEPRECATED_TAILWIND = re.compile(
    r"\b(?:bg-opacity-|text-opacity-|border-opacity-|flex-shrink-0|overflow-ellipsis)\b"
)

# Neutral tokens used to wrap each snippet into a full HTML document.
_NEUTRAL_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Snippet Validation</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
:root {
  --color-bg: #0a0a0a;
  --color-surface: #141414;
  --color-fg: #f5f5f5;
  --color-muted: #8b8b8b;
  --color-accent: #5eead4;
  --color-border: #262626;
  --font-display: 'Inter', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
}
body { background: var(--color-bg); color: var(--color-fg); font-family: var(--font-body); margin: 0; }
</style>
</head>
<body>
"""

_NEUTRAL_FOOT = "\n</body>\n</html>\n"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SnippetFinding:
    """Per-snippet validation finding."""

    section_type: str
    variant: str
    path: str
    overall_score: float = 0.0
    flags: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SnippetValidationReport:
    """Aggregated validation report across all snippets."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    broken_renders: list[str] = field(default_factory=list)
    low_scoring: list[dict[str, Any]] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    missing_css_vars: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    findings: list[SnippetFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_snippet(html: str) -> str:
    """Wrap a raw snippet in a full HTML document with neutral tokens."""
    return f"{_NEUTRAL_HEAD}{html}{_NEUTRAL_FOOT}"


def _static_checks(html: str) -> list[str]:
    """Return a list of static-analysis flags for a snippet."""
    flags: list[str] = []
    if not any(v in html for v in REQUIRED_CSS_VARS):
        flags.append("missing_css_vars")
    if PLACEHOLDER_PATTERNS.search(html):
        flags.append("placeholder_text")
    if DEPRECATED_TAILWIND.search(html):
        flags.append("deprecated_tailwind")
    return flags


async def _render_snippet(
    html: str,
    pool: PlaywrightPool | None,
) -> bytes:
    """Render a snippet to PNG bytes.

    Prefers the pool's fast path when available; falls back to the
    standalone :func:`clawdbot.renderer.render_html_to_png` otherwise.
    """
    wrapped = _wrap_snippet(html)
    if pool is not None and hasattr(pool, "render"):
        return await pool.render(wrapped)

    from clawdbot.renderer import render_html_to_png

    return await render_html_to_png(wrapped, full_page=False)


async def _score_snippet(png_bytes: bytes, section_type: str) -> float:
    """Score a rendered snippet via the VLM scorer.

    Returns the ``overall`` field of the resulting SectionScore, or 0.0
    if the scorer is unavailable.
    """
    try:
        from clawdbot.visual_scorer import score_section_quality
    except ImportError as exc:
        logger.warning("visual_scorer unavailable: %s", exc)
        return 0.0

    try:
        score = await score_section_quality(png_bytes, section_type)
    except Exception as exc:  # noqa: BLE001 - per-snippet VLM failures are survivable
        logger.warning("vlm score failed: %s", exc)
        return 0.0
    return float(getattr(score, "overall", 0.0) or 0.0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def validate_all_snippets(
    pool: PlaywrightPool | None = None,
    *,
    section_type: str | None = None,
) -> SnippetValidationReport:
    """Validate every snippet in ``soul/components/``.

    Args:
        pool: Optional pre-started Playwright pool. If None, each render
            spins up its own short-lived browser via ``render_html_to_png``.
        section_type: Optional filter — only validate snippets whose
            section_type matches.

    Returns:
        :class:`SnippetValidationReport` with per-snippet findings.
    """
    from clawdbot.snippet_registry import list_available_snippets, load_snippet

    report = SnippetValidationReport()
    available = list_available_snippets()

    for stype, variants in available.items():
        if section_type and stype != section_type:
            continue
        for variant in variants:
            report.total += 1
            finding = SnippetFinding(
                section_type=stype,
                variant=variant,
                path=f"soul/components/{stype}/{variant}.html",
            )
            try:
                snippet = load_snippet(stype, variant)
            except (FileNotFoundError, ValueError) as exc:
                finding.error = f"load error: {exc}"
                report.broken_renders.append(finding.path)
                report.failed += 1
                report.findings.append(finding)
                continue

            html = snippet.get("html", "")
            finding.flags.extend(_static_checks(html))

            try:
                png_bytes = await _render_snippet(html, pool)
                if not png_bytes:
                    raise RuntimeError("empty png bytes")
            except Exception as exc:  # noqa: BLE001 - capture render failures
                finding.error = f"render error: {exc}"
                finding.flags.append("broken_render")
                report.broken_renders.append(finding.path)
                report.failed += 1
                report.findings.append(finding)
                continue

            finding.overall_score = await _score_snippet(png_bytes, stype)
            if finding.overall_score < LOW_SCORE_THRESHOLD:
                finding.flags.append("low_score")
                report.low_scoring.append(
                    {
                        "path": finding.path,
                        "score": finding.overall_score,
                    }
                )

            # Categorize findings for the aggregated buckets.
            if "missing_css_vars" in finding.flags:
                report.missing_css_vars.append(finding.path)
            if "placeholder_text" in finding.flags:
                report.placeholders.append(finding.path)
            if "deprecated_tailwind" in finding.flags:
                report.deprecated.append(finding.path)

            if finding.flags:
                report.failed += 1
            else:
                report.passed += 1
            report.findings.append(finding)

    logger.info(
        "snippet validation: total=%d passed=%d failed=%d",
        report.total,
        report.passed,
        report.failed,
    )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate all ClawdBot component snippets",
    )
    parser.add_argument(
        "--section-type",
        type=str,
        default=None,
        help="Only validate snippets of this section type",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report to stdout",
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

    report = asyncio.run(validate_all_snippets(section_type=args.section_type))
    if args.json:
        print(
            json.dumps(
                {
                    "total": report.total,
                    "passed": report.passed,
                    "failed": report.failed,
                    "broken_renders": report.broken_renders,
                    "low_scoring": report.low_scoring,
                    "deprecated": report.deprecated,
                    "missing_css_vars": report.missing_css_vars,
                    "placeholders": report.placeholders,
                    "findings": [asdict(f) for f in report.findings],
                },
                indent=2,
            )
        )
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
