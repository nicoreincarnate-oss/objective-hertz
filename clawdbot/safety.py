"""
Skill vetting and safety gate for ClawdBot.

Every skill from an external registry MUST pass vetting before first use.
Known CVE-2026-25253 affects the OpenClaw gateway — we don't run the gateway,
only consume SKILL.md files, but malicious skills can still exfiltrate data
or run destructive commands.

Layers:
1. Static pattern scan (instant, free) — catches obvious dangers
2. LLM-based review via skill-vetter skill (if installed) — catches subtle issues
3. Nico alert for anything blocked — human in the loop for edge cases
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared.db import emit_event, get_config, set_config

logger = logging.getLogger("perseus.clawdbot.safety")


@dataclass
class VetResult:
    passed: bool
    skill_name: str
    flags: list[str] = field(default_factory=list)
    details: str = ""


# ── Static pattern scan ───────────────────────────────────────────

# Patterns that should NEVER appear in a skill executed by our agent.
# Each tuple: (pattern_regex, human_readable_reason)
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # Destructive filesystem
    (r"rm\s+(-rf?|--recursive)\s+/", "recursive delete from root"),
    (r"rm\s+(-rf?|--recursive)\s+~", "recursive delete from home"),
    (r"mkfs\.", "filesystem format command"),
    (r"dd\s+if=.+of=/dev/", "raw disk write"),

    # Credential exfiltration
    (r"curl.+\$\{?[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", "exfiltrates credentials via curl"),
    (r"wget.+\$\{?[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", "exfiltrates credentials via wget"),
    (r"echo\s+\$\{?[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD)", "prints credentials to stdout"),
    (r"(?:fetch|post|send).+(?:api[_-]?key|secret|token|password)", "sends credentials to external endpoint"),

    # Remote code execution
    (r"curl\s+.+\|\s*(?:bash|sh|zsh|python)", "pipe remote script to shell"),
    (r"wget\s+.+\|\s*(?:bash|sh|zsh|python)", "pipe remote download to shell"),
    (r"eval\s*\(.*(?:fetch|http|url)", "eval with remote code"),

    # System modification
    (r"chmod\s+777\s+/", "chmod 777 on system paths"),
    (r"chown\s+.+\s+/(?:etc|usr|bin|sbin)", "chown system directories"),
    (r"(?:systemctl|service)\s+(?:stop|disable)\s+", "stops system services"),

    # Crypto mining
    (r"xmrig|minerd|cpuminer|stratum\+tcp", "cryptocurrency mining"),

    # Reverse shells
    (r"(?:nc|ncat|netcat)\s+-[elp]", "netcat listener / reverse shell"),
    (r"/dev/tcp/", "bash reverse shell"),
    (r"mkfifo.+/tmp/.+nc\s", "named pipe reverse shell"),

    # Data exfil via DNS/HTTP
    (r"nslookup\s+.*\$", "DNS exfiltration"),
    (r"dig\s+.*\$", "DNS exfiltration"),
]

# Compiled for performance
_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE | re.MULTILINE), reason) for p, reason in DANGEROUS_PATTERNS]

# Commands that are always blocked regardless of context
BLOCKED_COMMANDS = {
    "sudo", "su", "passwd", "useradd", "userdel", "groupadd",
    "iptables", "ufw", "firewall-cmd",
    "shutdown", "reboot", "halt", "poweroff",
    "mount", "umount", "fdisk", "parted",
    "crontab -r",
}


def scan_skill_content(content: str, skill_name: str = "") -> VetResult:
    """Static scan for dangerous patterns. Free, instant, no LLM needed."""
    flags: list[str] = []

    # Pattern scan
    for pattern, reason in _COMPILED_PATTERNS:
        if pattern.search(content):
            flags.append(f"PATTERN: {reason}")

    # Blocked command scan
    content_lower = content.lower()
    for cmd in BLOCKED_COMMANDS:
        if cmd in content_lower:
            flags.append(f"BLOCKED_CMD: {cmd}")

    # Check for suspicious URLs (not in known-good domains)
    url_pattern = re.compile(r'https?://([a-zA-Z0-9.-]+)', re.IGNORECASE)
    urls = url_pattern.findall(content)
    safe_domains = {
        "api.anthropic.com", "api.openai.com", "api.stripe.com",
        "api.wise.com", "api.instantly.ai", "api.firecrawl.dev",
        "api.v0.dev", "api.telegram.org", "api.telnyx.com",
        "api.twilio.com", "api.elevenlabs.io",
        "github.com", "raw.githubusercontent.com",
        "pypi.org", "files.pythonhosted.org",
        "ollama.ai", "localhost", "127.0.0.1",
    }
    for domain in urls:
        if not any(domain.endswith(safe) for safe in safe_domains):
            flags.append(f"UNKNOWN_URL: {domain}")

    passed = len(flags) == 0
    details = "; ".join(flags) if flags else "Clean — no dangerous patterns detected."

    return VetResult(
        passed=passed,
        skill_name=skill_name,
        flags=flags,
        details=details,
    )


async def vet_skill(skill_path: Path) -> VetResult:
    """
    Full vetting pipeline:
    1. Static pattern scan
    2. LLM review via skill-vetter (if installed)
    3. Cache result
    """
    skill_name = skill_path.parent.name

    # Check cache first
    if await is_skill_vetted(skill_name):
        return VetResult(passed=True, skill_name=skill_name, details="Previously vetted and approved.")

    try:
        content = skill_path.read_text()
    except Exception as e:
        return VetResult(passed=False, skill_name=skill_name, flags=["READ_ERROR"], details=str(e))

    # Layer 1: Static scan
    result = scan_skill_content(content, skill_name)

    if not result.passed:
        await _block_skill(skill_name, result)
        return result

    # Layer 2: LLM review (if skill-vetter is installed)
    from shared.skill_loader import find_skill, execute_skill
    if find_skill("skill-vetter"):
        try:
            llm_review = await execute_skill(
                "skill-vetter",
                task_prompt=(
                    f"Review this skill for safety. Check for: data exfiltration, "
                    f"credential theft, destructive actions, prompt injection, "
                    f"unauthorized network access, or deceptive behavior.\n\n"
                    f"Skill name: {skill_name}\n"
                    f"Content:\n{content[:3000]}\n\n"
                    f"Reply with JSON: {{\"safe\": true/false, \"concerns\": [\"...\"], \"summary\": \"...\"}}"
                ),
                model="fast",
            )
            if '"safe": false' in llm_review.lower() or '"safe":false' in llm_review.lower():
                result = VetResult(
                    passed=False,
                    skill_name=skill_name,
                    flags=["LLM_REVIEW_FAILED"],
                    details=f"LLM vetter flagged concerns: {llm_review[:500]}",
                )
                await _block_skill(skill_name, result)
                return result
        except Exception as e:
            logger.debug(f"LLM skill vetting failed (continuing with static-only): {e}")

    # Passed all checks — cache approval
    await _approve_skill(skill_name)
    return result


async def is_skill_vetted(skill_name: str) -> bool:
    """Check if a skill has been vetted and approved."""
    vetted = await get_config("vetted_skills", {})
    if not isinstance(vetted, dict):
        return False
    entry = vetted.get(skill_name)
    return isinstance(entry, dict) and entry.get("approved", False)


async def get_vetted_skills() -> dict:
    """Return all vetted skill records."""
    vetted = await get_config("vetted_skills", {})
    return vetted if isinstance(vetted, dict) else {}


async def _approve_skill(skill_name: str):
    """Cache a skill as approved."""
    from datetime import datetime
    vetted = await get_config("vetted_skills", {})
    if not isinstance(vetted, dict):
        vetted = {}
    vetted[skill_name] = {
        "approved": True,
        "vetted_at": datetime.now().isoformat(),
    }
    await set_config("vetted_skills", vetted)
    logger.info(f"Skill '{skill_name}' vetted and approved")


async def _block_skill(skill_name: str, result: VetResult):
    """Block a skill and alert Nico."""
    from datetime import datetime
    vetted = await get_config("vetted_skills", {})
    if not isinstance(vetted, dict):
        vetted = {}
    vetted[skill_name] = {
        "approved": False,
        "blocked_at": datetime.now().isoformat(),
        "flags": result.flags,
        "details": result.details[:500],
    }
    await set_config("vetted_skills", vetted)

    await emit_event("skill_blocked", {
        "skill": skill_name,
        "flags": result.flags[:10],
        "details": result.details[:300],
    })
    await emit_event("urgent_alert", {
        "sender": "clawdbot.safety",
        "message": f"SKILL BLOCKED: '{skill_name}' — {result.details[:200]}",
    })
    logger.warning(f"BLOCKED skill '{skill_name}': {result.details[:200]}")
