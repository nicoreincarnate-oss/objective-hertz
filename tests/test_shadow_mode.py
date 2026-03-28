"""Tests for shadow mode pipeline gates.

Verifies that every pipeline stage with external side effects checks
the shadow_mode flag before making outbound calls.
"""

from pathlib import Path


def test_email_send_checks_shadow_mode():
    """email_send.py must check shadow_mode before any Instantly API calls."""
    code = Path("titan/pipeline/email_send.py").read_text()
    assert "shadow_mode" in code, "email_send.py missing shadow_mode check"
    assert "SHADOW:" in code, "email_send.py missing SHADOW log prefix"
    assert "shadow_email_send" in code, "email_send.py missing shadow_email_send event"


def test_build_site_checks_shadow_mode():
    """build_site.py must check shadow_mode before deploying to Netlify."""
    code = Path("titan/pipeline/build_site.py").read_text()
    assert "shadow_mode" in code, "build_site.py missing shadow_mode check"
    assert "SHADOW:" in code, "build_site.py missing SHADOW log prefix"
    assert "shadow_site_built" in code, "build_site.py missing shadow_site_built event"


def test_follow_up_checks_shadow_mode():
    """follow_up.py must check shadow_mode before sending follow-up emails."""
    code = Path("titan/pipeline/follow_up.py").read_text()
    assert "shadow_mode" in code, "follow_up.py missing shadow_mode check"
    assert "SHADOW:" in code, "follow_up.py missing SHADOW log prefix"


def test_invoice_checks_shadow_mode():
    """invoice.py must check shadow_mode before sending invoices."""
    code = Path("titan/pipeline/invoice.py").read_text()
    assert "shadow_mode" in code, "invoice.py missing shadow_mode check"
    assert "SHADOW:" in code, "invoice.py missing SHADOW log prefix"


def test_deploy_site_checks_shadow_mode():
    """deploy_site.py must check shadow_mode before verification loops."""
    code = Path("titan/pipeline/deploy_site.py").read_text()
    assert "shadow_mode" in code, "deploy_site.py missing shadow_mode check"
    assert "SHADOW:" in code, "deploy_site.py missing SHADOW log prefix"


def test_shadow_mode_in_init_db():
    """shadow_mode must be seeded as default in init-db.sql."""
    code = Path("scripts/init-db.sql").read_text()
    assert "shadow_mode" in code, "shadow_mode not seeded in init-db.sql"


def test_shadow_mode_in_war_room_config():
    """shadow_mode must be in the War Room config allowlist."""
    code = Path("hermes/web/app.py").read_text()
    assert "shadow_mode" in code
    # Check it's in _CONFIG_ALLOWLIST
    for line in code.splitlines():
        if "_CONFIG_ALLOWLIST" in line and "{" in line:
            # Multi-line set — check nearby lines
            start = code.index("_CONFIG_ALLOWLIST")
            block = code[start:start + 500]
            assert "shadow_mode" in block, "shadow_mode not in _CONFIG_ALLOWLIST"
            return
    # If single-line definition not found, search the whole allowlist block
    assert '"shadow_mode"' in code or "'shadow_mode'" in code


def test_email_prompt_templates_exist():
    """Three outreach prompt templates must exist in soul/templates/."""
    templates_dir = Path("soul/templates")
    assert templates_dir.exists(), "soul/templates/ directory missing"
    assert (templates_dir / "plumber_outreach.md").exists()
    assert (templates_dir / "electrician_outreach.md").exists()
    assert (templates_dir / "home_services_outreach.md").exists()
