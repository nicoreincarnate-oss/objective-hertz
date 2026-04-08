"""Layer 4: Per-daemon chain depth guard.

Forces tier escalation when sequential LLM calls in a daemon chain pass a
per-daemon threshold. Math: with ~87% per-step tool-call accuracy on 30B
local models, end-to-end success at depth N is 0.87^N.

  N=3 → 66% (Ruflo)
  N=4 → 57% (Titan, Openjarvis)
  N=5 → 50% (Clawdbot)
  N=8 → 32% (default)

Past these depths the verifier-catches-rest assumption breaks down. The depth
guard escalates to a stronger tier (cloud Sonnet) for the rest of the chain.

These are STARTING values from theoretical math. Phase 42.5 shadow mode
empirically tunes them per daemon based on observed task success.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shared.tiers import TierName, upgrade_tier

logger = logging.getLogger("perseus.verifier.depth_guard")


# Per-daemon depth caps — theoretical starting values, tuned in shadow mode.
PER_DAEMON_DEPTH_CAPS: dict[str, int] = {
    "ruflo":      3,   # Code editing chains: smallest cap, highest blast radius
    "titan":      4,   # Pipeline stages 1-6
    "openjarvis": 4,   # Workflow synthesis
    "clawdbot":   5,   # Section building
    "deerflow":   8,
    "conway":     6,
    "hermes":     8,
    "perseus":    8,
}

DEFAULT_DEPTH_CAP = 8


@dataclass
class DepthCheckResult:
    allowed: bool
    current_depth: int
    daemon_cap: int
    suggested_tier: TierName | None  # If allowed=False, escalate to this tier
    reason: str = ""


class DepthGuard:
    def __init__(self, caps: dict[str, int] | None = None):
        self.caps = caps or PER_DAEMON_DEPTH_CAPS

    def check(
        self,
        *,
        daemon: str,
        chain_depth: int,
        current_tier: TierName,
    ) -> DepthCheckResult:
        cap = self.caps.get(daemon, DEFAULT_DEPTH_CAP)
        if chain_depth < cap:
            return DepthCheckResult(
                allowed=True,
                current_depth=chain_depth,
                daemon_cap=cap,
                suggested_tier=None,
                reason="under cap",
            )
        new_tier = upgrade_tier(current_tier, reason=f"depth_guard_{daemon}_{chain_depth}>{cap}")
        return DepthCheckResult(
            allowed=False,
            current_depth=chain_depth,
            daemon_cap=cap,
            suggested_tier=new_tier,
            reason=f"depth {chain_depth} exceeds {daemon} cap of {cap}",
        )

    def update_cap(self, daemon: str, new_cap: int) -> None:
        """Update a daemon's cap (called after shadow mode tuning)."""
        old_cap = self.caps.get(daemon, DEFAULT_DEPTH_CAP)
        self.caps[daemon] = new_cap
        logger.info("Depth cap updated: %s %d → %d", daemon, old_cap, new_cap)


_default_guard = DepthGuard()


def check_depth(daemon: str, chain_depth: int, current_tier: TierName) -> DepthCheckResult:
    return _default_guard.check(daemon=daemon, chain_depth=chain_depth, current_tier=current_tier)


__all__ = ["DepthGuard", "DepthCheckResult", "PER_DAEMON_DEPTH_CAPS", "check_depth"]
