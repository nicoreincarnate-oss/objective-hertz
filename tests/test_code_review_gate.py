"""Tests for tools/code_review_gate.py."""

import asyncio
import os
import textwrap
from pathlib import Path

import pytest

from tools.code_review_gate import CodeReviewResult, review_site


VALID_HTML = textwrap.dedent("""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Test Site</title>
        <link rel="stylesheet" href="style.css">
    </head>
    <body>
        <header><h1>Hello</h1></header>
        <main>
            <img src="logo.png" alt="Logo">
            <a href="/">Home</a>
        </main>
    </body>
    </html>
""")

VALID_CSS = textwrap.dedent("""\
    body {
        margin: 0;
        font-family: sans-serif;
    }
    header {
        background: #333;
        color: #fff;
    }
""")


def _make_site(tmp_path: Path, files: dict[str, str]) -> str:
    """Create a temp site directory with the given files."""
    for name, content in files.items():
        fp = tmp_path / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    return str(tmp_path)


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_site_passes(tmp_path):
    """A well-formed site with required tags should pass."""
    site = _make_site(tmp_path, {
        "index.html": VALID_HTML,
        "style.css": VALID_CSS,
    })
    result = await review_site(site)
    assert result.passed is True
    assert len(result.errors) == 0
    assert result.file_count == 2
    assert result.feedback == ""


@pytest.mark.asyncio
async def test_missing_index_fails(tmp_path):
    """A site without index.html must fail."""
    site = _make_site(tmp_path, {
        "about.html": VALID_HTML,
        "style.css": VALID_CSS,
    })
    result = await review_site(site)
    assert result.passed is False
    assert any("index.html" in e for e in result.errors)


@pytest.mark.asyncio
async def test_missing_viewport_meta_error(tmp_path):
    """HTML missing the viewport meta tag should produce an error."""
    html_no_viewport = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <title>No Viewport</title>
        </head>
        <body><p>Hello</p></body>
        </html>
    """)
    site = _make_site(tmp_path, {"index.html": html_no_viewport})
    result = await review_site(site)
    assert result.passed is False
    assert any("viewport" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_external_script_security_error(tmp_path):
    """Loading a script from an untrusted domain must fail."""
    html_external = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>External Script</title>
        </head>
        <body>
            <script src="https://evil.example.com/payload.js"></script>
        </body>
        </html>
    """)
    site = _make_site(tmp_path, {"index.html": html_external})
    result = await review_site(site)
    assert result.passed is False
    assert any("untrusted domain" in e for e in result.errors)


@pytest.mark.asyncio
async def test_eval_in_js_security_error(tmp_path):
    """JS files with eval() must produce an error."""
    html = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Eval Test</title>
        </head>
        <body><p>Test</p></body>
        </html>
    """)
    js = 'var x = eval("1+1");'
    site = _make_site(tmp_path, {"index.html": html, "app.js": js})
    result = await review_site(site)
    assert result.passed is False
    assert any("eval()" in e for e in result.errors)


@pytest.mark.asyncio
async def test_size_check_over_limit(tmp_path):
    """A file exceeding 5 MB must fail."""
    html = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Big</title>
        </head>
        <body><p>Big site</p></body>
        </html>
    """)
    # Create a 6 MB file
    big_content = "x" * (6 * 1024 * 1024)
    site = _make_site(tmp_path, {"index.html": html, "huge.dat": big_content})
    result = await review_site(site)
    assert result.passed is False
    assert any("exceeds" in e and "MB" in e for e in result.errors)


@pytest.mark.asyncio
async def test_image_without_alt_warning(tmp_path):
    """An <img> without alt should produce a warning (not error)."""
    html_no_alt = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>No Alt</title>
        </head>
        <body>
            <img src="photo.jpg">
        </body>
        </html>
    """)
    site = _make_site(tmp_path, {"index.html": html_no_alt})
    result = await review_site(site)
    # Should pass (warnings don't block)
    assert result.passed is True
    assert any("alt" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_empty_site_dir_fails(tmp_path):
    """An empty directory must fail."""
    empty_dir = tmp_path / "empty_site"
    empty_dir.mkdir()
    result = await review_site(str(empty_dir))
    assert result.passed is False
    assert any("empty" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_feedback_message_format(tmp_path):
    """Feedback string must contain structured error info for regeneration."""
    html_bad = textwrap.dedent("""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>No charset or viewport</title>
        </head>
        <body>
            <script src="http://evil.com/bad.js"></script>
        </body>
        </html>
    """)
    site = _make_site(tmp_path, {"index.html": html_bad})
    result = await review_site(site)
    assert result.passed is False
    assert "CODE REVIEW FAILED" in result.feedback
    assert "ERROR" in result.feedback
    # Each error should appear as a numbered entry
    assert "ERROR 1:" in result.feedback
