from __future__ import annotations

import re

_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Original 6 patterns
    ("api_key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("github_token", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    ("slack_token", re.compile(r"xoxb-[0-9A-Za-z\-]+")),
    ("bearer_token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-.]{20,}")),

    # Stripe patterns (4) — per AEGIS audit requirement
    ("stripe_secret", re.compile(r"sk_live_[a-zA-Z0-9]{24,}")),
    ("stripe_test", re.compile(r"sk_test_[a-zA-Z0-9]{24,}")),
    ("stripe_publishable", re.compile(r"pk_live_[a-zA-Z0-9]{24,}")),
    ("stripe_restricted", re.compile(r"rk_live_[a-zA-Z0-9]{24,}")),

    # Telegram bot token
    ("telegram_token", re.compile(r"\d{8,10}:[a-zA-Z0-9_-]{35}")),

    # Netlify token
    ("netlify_token", re.compile(r"nfp_[a-zA-Z0-9]{40,}")),

    # Instantly API key
    ("instantly_key", re.compile(r"inst_[a-zA-Z0-9]{32,}")),

    # Database connection strings (Postgres)
    ("db_connection", re.compile(r"postgres(?:ql)?://[^\s'\"]{10,}")),

    # JWT tokens (three base64url segments)
    ("jwt_token", re.compile(r"eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}")),

    # Generic long hex secrets (e.g., webhook secrets, API keys)
    ("hex_secret", re.compile(
        r"(?:secret|key|token|password)[:=]\s*['\"]?[a-fA-F0-9]{32,}['\"]?",
        re.IGNORECASE,
    )),
]


class CredentialStripper:
    """Redacts credentials from text using compiled regex patterns."""

    def __init__(self) -> None:
        self._patterns = _CREDENTIAL_PATTERNS

    def strip(self, text: str) -> str:
        for label, pattern in self._patterns:
            text = pattern.sub(f"[REDACTED:{label}]", text)
        return text


def wrap_tool_output(tool_name: str, content: str, success: bool = True) -> str:
    status = "success" if success else "error"
    header = f'<tool_result name="{tool_name}" status="{status}">'
    return f"{header}\n{content}\n</tool_result>"
