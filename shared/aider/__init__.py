"""Aider architect+editor pattern for Perseus.

Splits code-modifying daemon work into:
- Architect (Claude Opus 4.6 or local-heavy via AirLLM) — plans the fix
- Editor (Qwen2.5-Coder-14B local or Sonnet 4.6) — applies the patch
- Verifier (pytest in sandbox) — validates the result

Cost savings: 60-80% on Ruflo and Clawdbot vs full-Opus loops.
Quality preservation: Architect's plan constrains the editor.
"""

from shared.aider.ruflo_loop import run_ruflo_aider_loop, RufloAiderResult
from shared.aider.clawdbot_loop import run_clawdbot_aider_loop, ClawdbotAiderResult

__all__ = [
    "run_ruflo_aider_loop",
    "RufloAiderResult",
    "run_clawdbot_aider_loop",
    "ClawdbotAiderResult",
]
