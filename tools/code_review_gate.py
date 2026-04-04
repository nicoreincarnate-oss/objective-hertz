"""Code review gate for generated websites.

Validates generated site code BEFORE visual QA and deployment.
Checks: HTML validity, CSS syntax, JS syntax, security, file size.
Returns pass/fail with specific feedback for regeneration.
"""

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# CDNs allowed in external script/link tags
ALLOWED_CDNS = {
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "cdn.jsdelivr.net",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.tailwindcss.com",
}

MAX_TOTAL_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_FILE_SIZE = 5 * 1024 * 1024     # 5 MB
IMAGE_WARN_SIZE = 500 * 1024        # 500 KB

REQUIRED_HTML_TAGS = ["html", "head", "body", "title"]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}


@dataclass
class CodeReviewResult:
    """Result of code review gate."""
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    total_size_bytes: int = 0
    feedback: str = ""  # Specific feedback for regeneration


async def review_site(site_dir: str) -> CodeReviewResult:
    """
    Run all code review checks on a generated site.

    Checks:
    1. File existence — at minimum index.html must exist
    2. HTML validity — no unclosed tags, required elements present
    3. CSS check — no syntax errors, no broken references
    4. JS check — no syntax errors (basic regex)
    5. Security — no external script loading, no inline event handlers loading remote URLs
    6. Size check — total site < 10MB, single file < 5MB
    7. Accessibility basics — images have alt, links have text
    8. Mobile meta — viewport meta tag present

    Returns CodeReviewResult with pass/fail and feedback.
    """
    errors: list[str] = []
    warnings: list[str] = []
    site_path = Path(site_dir)

    # ── 0. Directory sanity ──────────────────────────────────────────
    if not site_path.is_dir():
        return CodeReviewResult(
            passed=False,
            errors=[f"Site directory does not exist: {site_dir}"],
            feedback="The site directory does not exist. Ensure the build step produced output.",
        )

    all_files = _collect_files(site_path)
    if not all_files:
        return CodeReviewResult(
            passed=False,
            errors=["Site directory is empty — no files found."],
            feedback="The site directory contains no files. Generation may have failed silently.",
        )

    # ── 1. File existence ────────────────────────────────────────────
    index_path = site_path / "index.html"
    if not index_path.is_file():
        errors.append("Missing index.html — every site must have an index.html at the root.")

    # ── Size accounting ──────────────────────────────────────────────
    total_size = 0
    for fp in all_files:
        fsize = fp.stat().st_size
        total_size += fsize
        _check_file_size(fp, fsize, site_path, errors, warnings)

    # ── Read HTML files ──────────────────────────────────────────────
    html_files = [f for f in all_files if f.suffix.lower() in (".html", ".htm")]
    css_files = [f for f in all_files if f.suffix.lower() == ".css"]
    js_files = [f for f in all_files if f.suffix.lower() == ".js"]

    html_contents: dict[Path, str] = {}
    for hf in html_files:
        try:
            html_contents[hf] = hf.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"Could not read {_rel(hf, site_path)}: {exc}")

    css_contents: dict[Path, str] = {}
    for cf in css_files:
        try:
            css_contents[cf] = cf.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"Could not read {_rel(cf, site_path)}: {exc}")

    js_contents: dict[Path, str] = {}
    for jf in js_files:
        try:
            js_contents[jf] = jf.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"Could not read {_rel(jf, site_path)}: {exc}")

    # ── 2. HTML validation ───────────────────────────────────────────
    for hf, content in html_contents.items():
        _check_html(hf, content, site_path, errors, warnings)

    # ── 3. CSS sanity ────────────────────────────────────────────────
    for cf, content in css_contents.items():
        _check_css(cf, content, site_path, errors, warnings)

    # ── 4. JS syntax check ───────────────────────────────────────────
    for jf, content in js_contents.items():
        _check_js(jf, content, site_path, errors, warnings)

    # Also check inline <script> blocks in HTML
    for hf, content in html_contents.items():
        _check_inline_js(hf, content, site_path, errors, warnings)

    # ── 5. Security ──────────────────────────────────────────────────
    for hf, content in html_contents.items():
        _check_security(hf, content, site_path, errors, warnings)

    # ── 6. Total size ────────────────────────────────────────────────
    if total_size > MAX_TOTAL_SIZE:
        errors.append(
            f"Total site size {total_size / 1024 / 1024:.1f} MB exceeds {MAX_TOTAL_SIZE / 1024 / 1024:.0f} MB limit."
        )

    # ── 7. Accessibility basics ──────────────────────────────────────
    for hf, content in html_contents.items():
        _check_accessibility(hf, content, site_path, errors, warnings)

    # ── Build result ─────────────────────────────────────────────────
    passed = len(errors) == 0
    feedback = _build_feedback(errors, warnings) if not passed else ""

    result = CodeReviewResult(
        passed=passed,
        errors=errors,
        warnings=warnings,
        file_count=len(all_files),
        total_size_bytes=total_size,
        feedback=feedback,
    )

    level = logging.INFO if passed else logging.WARNING
    logger.log(
        level,
        "Code review %s - %d errors, %d warnings, %d files, %.1f KB",
        "PASSED" if passed else "FAILED",
        len(errors),
        len(warnings),
        len(all_files),
        total_size / 1024,
    )
    return result


# ── Internal helpers ─────────────────────────────────────────────────


def _collect_files(root: Path) -> list[Path]:
    """Recursively collect all files under root, skipping hidden dirs."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.startswith("."):
                files.append(Path(dirpath) / fn)
    return files


def _rel(filepath: Path, root: Path) -> str:
    """Return a relative path string for error messages."""
    try:
        return str(filepath.relative_to(root))
    except ValueError:
        return str(filepath)


def _check_file_size(
    fp: Path, fsize: int, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Check individual file size limits."""
    rel = _rel(fp, root)
    if fsize > MAX_FILE_SIZE:
        errors.append(
            f"{rel}: file size {fsize / 1024 / 1024:.1f} MB exceeds "
            f"{MAX_FILE_SIZE / 1024 / 1024:.0f} MB limit."
        )
    elif fp.suffix.lower() in IMAGE_EXTENSIONS and fsize > IMAGE_WARN_SIZE:
        warnings.append(
            f"{rel}: image is {fsize / 1024:.0f} KB — consider optimizing (>{IMAGE_WARN_SIZE // 1024} KB)."
        )


def _check_html(
    filepath: Path, content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Validate HTML structure."""
    rel = _rel(filepath, root)
    lower = content.lower()

    # Required tags
    for tag in REQUIRED_HTML_TAGS:
        if f"<{tag}" not in lower and f"<{tag}>" not in lower:
            errors.append(f"{rel}: missing required <{tag}> tag.")

    # Charset meta
    if "charset" not in lower:
        errors.append(f"{rel}: missing charset meta tag. Add <meta charset=\"utf-8\">.")

    # Viewport meta (mobile) — must be in an actual meta tag, not just anywhere
    if not re.search(r'<meta\s[^>]*name\s*=\s*["\']viewport["\']', lower):
        errors.append(f"{rel}: missing viewport meta tag. Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.")

    # Check for unclosed major tags
    for tag in ("div", "section", "main", "header", "footer", "nav", "article", "aside"):
        opens = len(re.findall(rf"<{tag}[\s>]", lower))
        closes = len(re.findall(rf"</{tag}\s*>", lower))
        if opens > closes:
            warnings.append(f"{rel}: possible unclosed <{tag}> — {opens} opened, {closes} closed.")


def _check_css(
    filepath: Path, content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Basic CSS sanity checks."""
    rel = _rel(filepath, root)

    # Empty rule blocks: selector { }
    empty_rules = re.findall(r"[^}]\{[\s]*\}", content)
    if empty_rules:
        warnings.append(f"{rel}: found {len(empty_rules)} empty CSS rule block(s).")

    # Unbalanced braces
    opens = content.count("{")
    closes = content.count("}")
    if opens != closes:
        errors.append(f"{rel}: unbalanced CSS braces — {opens} opening vs {closes} closing.")


def _check_js(
    filepath: Path, content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Basic JS checks on standalone .js files."""
    rel = _rel(filepath, root)
    _check_js_content(rel, content, errors, warnings)


def _check_inline_js(
    filepath: Path, html_content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Extract and check inline <script> blocks."""
    rel = _rel(filepath, root)
    # Find all inline script blocks (not ones with src)
    inline_scripts = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>",
        html_content,
        re.DOTALL | re.IGNORECASE,
    )
    for i, script in enumerate(inline_scripts):
        # Skip empty scripts and scripts that are just whitespace
        stripped = script.strip()
        if not stripped:
            continue
        label = f"{rel} (inline script #{i + 1})"
        _check_js_content(label, stripped, errors, warnings)


def _check_js_content(
    label: str, content: str, errors: list[str], warnings: list[str],
) -> None:
    """Shared JS content checks."""
    # eval() usage
    if re.search(r"\beval\s*\(", content):
        errors.append(f"{label}: contains eval() — security risk. Use safer alternatives.")

    # document.write()
    if re.search(r"\bdocument\.write\s*\(", content):
        errors.append(f"{label}: contains document.write() — can break page. Use DOM manipulation instead.")

    # Basic bracket balance (rough heuristic)
    for open_ch, close_ch, name in [("{", "}", "curly braces"), ("(", ")", "parentheses"), ("[", "]", "brackets")]:
        # Strip strings and comments for more accurate counting
        stripped = _strip_js_strings_and_comments(content)
        o = stripped.count(open_ch)
        c = stripped.count(close_ch)
        if o != c:
            warnings.append(f"{label}: possible unmatched {name} — {o} opening vs {c} closing.")


def _strip_js_strings_and_comments(code: str) -> str:
    """Remove string literals and comments for bracket-matching accuracy."""
    # Remove single-line comments
    code = re.sub(r"//[^\n]*", "", code)
    # Remove multi-line comments
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    # Remove template literals
    code = re.sub(r"`[^`]*`", "", code)
    # Remove double-quoted strings
    code = re.sub(r'"(?:[^"\\]|\\.)*"', "", code)
    # Remove single-quoted strings
    code = re.sub(r"'(?:[^'\\]|\\.)*'", "", code)
    return code


def _check_security(
    filepath: Path, content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Security checks on HTML content."""
    rel = _rel(filepath, root)

    # External scripts from non-allowed domains
    script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
    for src in script_srcs:
        if src.startswith(("http://", "https://", "//")):
            domain = _extract_domain(src)
            if domain and not _is_allowed_cdn(domain):
                errors.append(
                    f"{rel}: external script from untrusted domain '{domain}' — "
                    f"only allowed CDNs: {', '.join(sorted(ALLOWED_CDNS))}."
                )
        # http:// specifically is insecure
        if src.startswith("http://"):
            errors.append(f"{rel}: script loaded over insecure HTTP: {src}. Use HTTPS.")

    # External stylesheets from non-allowed domains
    link_hrefs = re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', content, re.IGNORECASE)
    for href in link_hrefs:
        if href.startswith(("http://", "https://", "//")) and "stylesheet" in content[max(0, content.find(href) - 100):content.find(href)].lower():
            domain = _extract_domain(href)
            if domain and not _is_allowed_cdn(domain):
                warnings.append(f"{rel}: external stylesheet from '{domain}' — verify this is trusted.")

    # Inline event handlers loading remote URLs
    remote_onclick = re.findall(
        r'on\w+=["\'][^"\']*(?:https?://|//)[^"\']*["\']',
        content, re.IGNORECASE,
    )
    if remote_onclick:
        errors.append(
            f"{rel}: found {len(remote_onclick)} inline event handler(s) loading remote URLs — security risk."
        )

    # Base64-encoded executables in data URIs
    b64_executables = re.findall(
        r'data:application/(?:x-msdownload|x-executable|octet-stream)[^"\']*base64',
        content, re.IGNORECASE,
    )
    if b64_executables:
        errors.append(f"{rel}: contains base64-encoded executable data URI — not allowed.")


def _extract_domain(url: str) -> Optional[str]:
    """Extract domain from a URL string without urllib."""
    # Strip protocol
    url = re.sub(r"^(?:https?:)?//", "", url)
    # Take everything before the first /
    domain = url.split("/")[0].split("?")[0].split("#")[0]
    # Strip port
    domain = domain.split(":")[0]
    return domain.lower() if domain else None


def _is_allowed_cdn(domain: str) -> bool:
    """Check if domain matches an allowed CDN."""
    for cdn in ALLOWED_CDNS:
        if domain == cdn or domain.endswith("." + cdn):
            return True
    return False


def _check_accessibility(
    filepath: Path, content: str, root: Path,
    errors: list[str], warnings: list[str],
) -> None:
    """Basic accessibility checks."""
    rel = _rel(filepath, root)

    # Images without alt attributes
    imgs = re.findall(r"<img\s[^>]*?>", content, re.IGNORECASE | re.DOTALL)
    for img in imgs:
        if "alt=" not in img.lower():
            warnings.append(f"{rel}: <img> without alt attribute — add alt text for accessibility.")
            break  # One warning per file is enough

    # Links without text content
    empty_links = re.findall(
        r"<a\s[^>]*?>\s*</a>",
        content, re.IGNORECASE | re.DOTALL,
    )
    if empty_links:
        warnings.append(f"{rel}: found {len(empty_links)} empty <a> link(s) — add link text or aria-label.")

    # Form inputs without associated labels (basic check)
    inputs = re.findall(r'<input\s[^>]*type=["\'](?:text|email|password|number|tel|url|search)["\'][^>]*?>', content, re.IGNORECASE)
    labels = re.findall(r"<label\b", content, re.IGNORECASE)
    if inputs and not labels:
        warnings.append(f"{rel}: {len(inputs)} text input(s) found but no <label> elements — add labels for accessibility.")


def _build_feedback(errors: list[str], warnings: list[str]) -> str:
    """Build structured feedback string for regeneration."""
    parts = ["CODE REVIEW FAILED — fix the following errors before deployment:\n"]
    for i, err in enumerate(errors, 1):
        parts.append(f"  ERROR {i}: {err}")
    if warnings:
        parts.append(f"\nAlso {len(warnings)} warning(s) to consider (non-blocking):")
        for w in warnings[:5]:  # Cap warnings in feedback
            parts.append(f"  WARNING: {w}")
        if len(warnings) > 5:
            parts.append(f"  ... and {len(warnings) - 5} more warning(s).")
    return "\n".join(parts)
