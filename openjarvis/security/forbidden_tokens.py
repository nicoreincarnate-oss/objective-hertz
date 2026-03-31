"""Forbidden token scanner — detects secrets and PII leaking into LLM output.

Source: Paperclip check-forbidden-tokens.mjs (MIT), adapted for Python.
Feature flag: FORBIDDEN_TOKEN_SCAN_ENABLED

Phase 12: Foundation Patterns (FP-04)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger("openjarvis.security.forbidden_tokens")


class TokenMatch(NamedTuple):
    pattern_name: str
    matched_text: str
    position: int


# OS username detection
_OS_USERNAME = os.environ.get("USER", os.environ.get("USERNAME", ""))

# Core patterns (compiled once at import)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # API key formats
    ("openai_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}")),
    ("stripe_secret", re.compile(r"sk_(?:live|test)_[a-zA-Z0-9]{20,}")),
    ("stripe_publishable", re.compile(r"pk_(?:live|test)_[a-zA-Z0-9]{20,}")),
    ("stripe_restricted", re.compile(r"rk_live_[a-zA-Z0-9]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[ps]_[a-zA-Z0-9]{36}")),
    ("netlify_token", re.compile(r"nfp_[a-zA-Z0-9]{40,}")),
    ("telegram_bot_token", re.compile(r"\d{8,10}:[a-zA-Z0-9_-]{35}")),
    ("instantly_key", re.compile(r"inst_[a-zA-Z0-9]{20,}")),
    ("jwt_token", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+")),
    # Database connection strings
    ("postgres_dsn", re.compile(r"postgresql?://[^:]+:[^@]+@[^\s]+")),
    # .env value patterns (KEY=value on same line)
    ("env_leak", re.compile(r"(?:API_KEY|SECRET|PASSWORD|TOKEN|DSN)\s*=\s*\S{8,}")),
    # Common injection markers
    ("injection_marker", re.compile(r"(?:<\|system\|>|<\|user\|>|<\|assistant\|>|\[INST\]|\[/INST\])")),
]


def _load_allowlist(project_root: Path | None = None) -> set[str]:
    """Load patterns to ignore from .forbidden-tokens-allow."""
    allow_path = (project_root or Path.cwd()) / ".forbidden-tokens-allow"
    if not allow_path.exists():
        return set()
    lines = allow_path.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def scan(
    text: str,
    *,
    include_username: bool = True,
    project_root: Path | None = None,
) -> list[TokenMatch]:
    """Scan text for forbidden tokens. Returns list of matches."""
    allowlist = _load_allowlist(project_root)
    matches: list[TokenMatch] = []

    for name, pattern in _PATTERNS:
        if name in allowlist:
            continue
        for m in pattern.finditer(text):
            matches.append(TokenMatch(name, m.group()[:40] + "...", m.start()))

    # OS username check (only if username is 3+ chars to avoid false positives)
    if include_username and _OS_USERNAME and len(_OS_USERNAME) >= 3:
        if "os_username" not in allowlist:
            for m in re.finditer(re.escape(_OS_USERNAME), text, re.IGNORECASE):
                matches.append(TokenMatch("os_username", _OS_USERNAME, m.start()))

    return matches


def scan_or_raise(text: str, **kwargs) -> str:
    """Scan and raise ValueError if any forbidden tokens found.

    Returns text unchanged if clean.
    """
    hits = scan(text, **kwargs)
    if hits:
        names = ", ".join(sorted({h.pattern_name for h in hits}))
        raise ValueError(f"Forbidden tokens detected: {names} ({len(hits)} match(es))")
    return text
