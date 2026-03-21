"""Static checks for small cleanup and health endpoint hardening."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_review_mode_removes_redundant_success_branch():
    code = (ROOT / "titan/review_mode.py").read_text()
    start = code.index("async def _send_approved_email")
    end = code.index("async def _send_approved_proposal")
    email_code = code[start:end]
    assert "if not success:" in email_code
    assert "if success:" not in email_code


def test_follow_up_no_longer_has_redundant_exception_tuple():
    code = (ROOT / "titan/pipeline/follow_up.py").read_text()
    assert "except Exception as e:" in code
    assert "except (ImportError, Exception)" not in code


def test_health_endpoint_checks_db_and_agent_liveness():
    code = (ROOT / "hermes/web/app.py").read_text()
    assert 'await fetch_val("SELECT 1")' in code
    assert '"db_ok": db_ok' in code
    assert 'status = "ok" if db_ok and agents_ok else "degraded"' in code
