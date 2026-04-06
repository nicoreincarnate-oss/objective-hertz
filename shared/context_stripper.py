"""Context stripping for lightweight monitoring agents.

Reduces full system context to essential information for agents that only
need instruction, system status, and recent errors — stripping pipeline
details, campaign data, financial context, memory indexes, and DNA docs.

Feature flag: ANATOMY_MODEL_TIERING (env var).
"""

from __future__ import annotations

import os
import re


def is_enabled() -> bool:
    """Check whether the ANATOMY_MODEL_TIERING feature flag is active."""
    return os.environ.get("ANATOMY_MODEL_TIERING", "").lower() in ("true", "1")


# Section headers that signal content to STRIP (case-insensitive).
# These cover pipeline details, campaign data, financial context,
# full memory index, and DNA docs.
_STRIP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#+\s*(pipeline|campaign|financial|revenue|budget|pricing)", re.IGNORECASE),
    re.compile(r"^#+\s*(memory\s*index|dna|agent\s*dna|personality|soul)", re.IGNORECASE),
    re.compile(r"^#+\s*(conway|wallet|economics|ledger|x402)", re.IGNORECASE),
    re.compile(r"^#+\s*(titan|lead|prospect|email\s*compose)", re.IGNORECASE),
    re.compile(r"^#+\s*(site\s*build|template|hosting|coolify)", re.IGNORECASE),
    re.compile(r"^#+\s*(lora|training|weight\s*directive)", re.IGNORECASE),
]

# Section headers that signal content to KEEP (case-insensitive).
_KEEP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#+\s*(instruction|standing\s*instruction|task|objective)", re.IGNORECASE),
    re.compile(r"^#+\s*(status|system\s*status|health|runtime)", re.IGNORECASE),
    re.compile(r"^#+\s*(error|recent\s*error|failure|alert|warning)", re.IGNORECASE),
    re.compile(r"^#+\s*(monitor|observation|anomal)", re.IGNORECASE),
]


def _is_section_header(line: str) -> bool:
    """Return True if the line is a Markdown heading."""
    return bool(line.strip().startswith("#"))


def _should_strip_section(header: str) -> bool:
    """Return True if a section header matches a strip pattern."""
    return any(p.search(header) for p in _STRIP_PATTERNS)


def _should_keep_section(header: str) -> bool:
    """Return True if a section header matches a keep pattern."""
    return any(p.search(header) for p in _KEEP_PATTERNS)


def strip_context(full_context: str, budget_chars: int = 3000) -> str:
    """Strip system context down to essentials for lightweight agents.

    Keeps:
      - Agent instruction / task / objective sections
      - System status / health / runtime sections
      - Recent errors / failures / alerts
      - Content before the first section header (preamble)

    Strips:
      - Pipeline details, campaign data, financial context
      - Full memory index, DNA docs, personality/soul docs
      - Conway/wallet/economics, Titan/lead, site build/hosting
      - LoRA/training sections

    Truncates at last newline boundary within *budget_chars* to avoid
    mid-word cuts.

    Args:
        full_context: The full assembled system prompt.
        budget_chars: Maximum character budget for the output.

    Returns:
        Stripped context string, guaranteed <= budget_chars.
    """
    if not full_context:
        return ""

    if len(full_context) <= budget_chars:
        return full_context

    lines = full_context.split("\n")
    kept_lines: list[str] = []
    in_strip_section = False

    for line in lines:
        if _is_section_header(line):
            if _should_strip_section(line):
                in_strip_section = True
                continue
            elif _should_keep_section(line):
                in_strip_section = False
                kept_lines.append(line)
                continue
            else:
                # Unknown section: keep by default but stop stripping
                in_strip_section = False
                kept_lines.append(line)
                continue

        if not in_strip_section:
            kept_lines.append(line)

    result = "\n".join(kept_lines)

    # Truncate to budget at last newline boundary
    if len(result) <= budget_chars:
        return result

    truncated = result[:budget_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        return truncated[:last_newline]
    return truncated
