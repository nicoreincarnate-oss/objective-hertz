"""Regression tests for Telegram command authentication."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_destructive_telegram_commands_require_second_secret():
    code = (ROOT / "hermes" / "telegram_bot.py").read_text()

    assert "TELEGRAM_ADMIN_SECRET" in code
    assert "hmac.compare_digest" in code
    assert "_require_destructive_auth(" in code
    assert "Usage: /approve <review_id> <admin_secret>" in code
    assert "Usage: /pause <admin_secret>" in code


def test_all_telegram_commands_are_chat_scoped():
    code = (ROOT / "hermes" / "telegram_bot.py").read_text()

    for command in ("cmd_status", "cmd_leads", "cmd_revenue", "cmd_review", "cmd_help"):
        assert "if not await _require_chat_access(update):" in code, f"{command} should enforce chat access"
