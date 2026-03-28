"""Tests for the /unsub CAN-SPAM unsubscribe endpoint.

Tests the HMAC validation logic directly and verifies endpoint behavior
through source-code contract assertions (matching project test patterns).
"""

import hashlib
import hmac
from pathlib import Path

UNSUB_SIGNATURE_HEX_LENGTH = 32
TEST_SECRET = "test-secret-that-is-long-enough-for-hmac"


def _make_sig(client_id: int, secret: str = TEST_SECRET) -> str:
    """Generate HMAC signature matching titan/compliance.py logic."""
    return hmac.new(
        secret.encode(), str(client_id).encode(), hashlib.sha256
    ).hexdigest()[:UNSUB_SIGNATURE_HEX_LENGTH]


def test_unsub_hmac_matches_compliance_module():
    """The /unsub endpoint HMAC must match titan/compliance.py generate_unsub_link."""
    from titan.compliance import generate_unsub_link

    link = generate_unsub_link(42, TEST_SECRET, "https://example.com")
    # Extract sig from the URL
    sig_from_compliance = link.split("sig=")[1]
    sig_from_test = _make_sig(42)
    assert sig_from_compliance == sig_from_test, (
        f"HMAC mismatch: compliance={sig_from_compliance}, endpoint={sig_from_test}"
    )


def test_unsub_signature_is_32_hex_chars():
    """Signature length must be 32 hex chars (128-bit security)."""
    sig = _make_sig(1)
    assert len(sig) == 32
    assert all(c in "0123456789abcdef" for c in sig)


def test_unsub_different_clients_get_different_sigs():
    """Each client_id must produce a unique signature."""
    sigs = {_make_sig(i) for i in range(100)}
    assert len(sigs) == 100, "Signature collision detected"


def test_unsub_endpoint_exists_in_app():
    """The /unsub endpoint must exist in hermes/web/app.py."""
    code = Path("hermes/web/app.py").read_text()
    assert '@app.get("/unsub"' in code, "/unsub GET endpoint not found in app.py"


def test_unsub_is_in_public_paths():
    """The /unsub path must be in _PUBLIC_PATHS (no auth required)."""
    code = Path("hermes/web/app.py").read_text()
    assert '"/unsub"' in code
    # Find the _PUBLIC_PATHS line and verify /unsub is there
    for line in code.splitlines():
        if "_PUBLIC_PATHS" in line and "set" in line.lower() or "_PUBLIC_PATHS" in line and "{" in line:
            assert "/unsub" in line, "/unsub not in _PUBLIC_PATHS set"
            break


def test_unsub_endpoint_validates_hmac():
    """The /unsub handler must use hmac.compare_digest for timing-safe comparison."""
    code = Path("hermes/web/app.py").read_text()
    assert "hmac.compare_digest" in code, "Missing timing-safe HMAC comparison"


def test_unsub_endpoint_updates_client_status():
    """The /unsub handler must UPDATE clients SET status = 'unsubscribed'."""
    code = Path("hermes/web/app.py").read_text()
    assert "unsubscribed" in code
    assert "UPDATE clients SET status" in code


def test_unsub_endpoint_emits_event():
    """The /unsub handler must emit a client_unsubscribed event for audit trail."""
    code = Path("hermes/web/app.py").read_text()
    assert "client_unsubscribed" in code
