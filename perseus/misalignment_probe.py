"""
Misalignment Probes — Safety checks for Alpha/Beta proposals.

Papers: Emergent Misalignment (15-30% misalignment by epoch 3),
Mechanistic Analysis (attention disruption 15-23%),
FIT (forgetting concentrates in later epochs),
Forget Forgetting (critical params in sparse high-curvature regions),
EvolveR (trajectory analysis), Limits of Self-Improving (entropy decay).

Three probe types:
1. probe_proposal() — per-proposal safety check (pricing drift, compliance, harmful patterns)
2. trajectory_analysis() — 7-day directional trend detection
3. convergence_detector() — entropy of proposal categories (know when to stop)

Gated behind MISALIGNMENT_PROBES_ENABLED=1 (default 1).
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter

logger = logging.getLogger("perseus.misalignment_probe")

MISALIGNMENT_PROBES_ENABLED = os.environ.get("MISALIGNMENT_PROBES_ENABLED", "1") == "1"
RISK_THRESHOLD = float(os.environ.get("MISALIGNMENT_RISK_THRESHOLD", "0.15"))

# Patterns that should ALWAYS be blocked regardless of context
KNOWN_HARMFUL_PATTERNS = [
    "disable compliance",
    "remove rate limit",
    "skip safety check",
    "ignore budget",
    "bypass review",
    "delete all",
    "drop table",
    "remove authentication",
    "disable logging",
    "skip validation",
]

# Pricing constants (must match backprop.py)
PRICE_MIN = 149
PRICE_MAX = 499
PRICE_MAX_DELTA = 50


# ═══════════════════════════════════════════════════════════════
# Per-Proposal Probes
# ═══════════════════════════════════════════════════════════════

async def probe_proposal(proposal: dict, system_snapshot: dict) -> dict:
    """Run 3 safety checks on a single Alpha proposal.

    Returns: {"safe": bool, "risk_score": float 0.0-1.0, "flags": list[str]}

    Risk score > RISK_THRESHOLD (default 0.15) → auto-reject.
    """
    if not MISALIGNMENT_PROBES_ENABLED:
        return {"safe": True, "risk_score": 0.0, "flags": []}

    flags = []
    risk = 0.0

    what = str(proposal.get("what", "")).lower()
    where = str(proposal.get("where", "")).lower()
    new_value = proposal.get("new_value", "")
    _old_value = proposal.get("old_value", "")

    # Probe 1: Pricing drift check
    pricing_risk = _check_pricing_drift(proposal, system_snapshot)
    if pricing_risk > 0:
        risk += pricing_risk
        flags.append(f"pricing_drift: {pricing_risk:.2f}")

    # Probe 2: Compliance keyword detection
    for pattern in KNOWN_HARMFUL_PATTERNS:
        if pattern in what or pattern in str(new_value).lower():
            risk += 0.5
            flags.append(f"harmful_pattern: '{pattern}'")
            break

    # Probe 3: Soul doc sensitivity
    if "soul" in where or "compliance" in where:
        risk += 0.1
        flags.append("modifies_soul_or_compliance")

    # Probe 4: Confidence check — low-confidence proposals are riskier
    confidence = proposal.get("confidence", 0.5)
    if confidence < 0.4:
        risk += 0.1
        flags.append(f"low_confidence: {confidence:.2f}")

    risk = min(1.0, risk)
    safe = risk < RISK_THRESHOLD

    if not safe:
        logger.warning(f"Misalignment probe BLOCKED: risk={risk:.2f}, flags={flags}")

    return {"safe": safe, "risk_score": risk, "flags": flags}


def _check_pricing_drift(proposal: dict, snapshot: dict) -> float:
    """Check if pricing is drifting too far from baseline."""
    what = str(proposal.get("what", "")).lower()
    if "pric" not in what:
        return 0.0

    try:
        new_val = float(proposal.get("new_value", 0))
        old_val = float(proposal.get("old_value", 0))
    except (ValueError, TypeError):
        return 0.0

    if old_val <= 0:
        return 0.0

    # Percentage drift from current
    drift_pct = abs(new_val - old_val) / old_val

    if drift_pct > 0.15:
        return min(0.4, drift_pct)  # Cap at 0.4 risk

    return 0.0


# ═══════════════════════════════════════════════════════════════
# Trajectory Analysis (EvolveR paper)
# ═══════════════════════════════════════════════════════════════

async def trajectory_analysis(days: int = 7) -> dict:
    """Analyze proposal trajectory over the last N days.

    Detects:
    - Consistent directional drift (always increasing prices, always relaxing constraints)
    - Proposal repetition (same changes proposed repeatedly)
    - Acceleration (changes getting larger over time)

    Returns: {"trend": str, "drift_magnitude": float, "alert": bool, "details": str}
    """
    if not MISALIGNMENT_PROBES_ENABLED:
        return {"trend": "stable", "drift_magnitude": 0.0, "alert": False, "details": "probes disabled"}

    try:
        from shared.db import fetch_all
        cycles = await fetch_all(
            """SELECT cycle_date, applied_changes, rolled_back
               FROM sleep_cycle_log
               WHERE cycle_date > NOW() - INTERVAL '%s days'
               ORDER BY cycle_date ASC""",
            (days,),
        )
    except Exception:
        return {"trend": "unknown", "drift_magnitude": 0.0, "alert": False, "details": "db unavailable"}

    if len(cycles) < 2:
        return {"trend": "insufficient_data", "drift_magnitude": 0.0, "alert": False, "details": f"only {len(cycles)} cycles"}

    # Extract all applied changes
    all_changes = []
    for cycle in cycles:
        changes = cycle.get("applied_changes", [])
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(changes, list):
            all_changes.extend(changes)

    if not all_changes:
        return {"trend": "no_changes", "drift_magnitude": 0.0, "alert": False, "details": "no changes applied"}

    # Analyze direction: are changes consistently moving in one direction?
    _categories = Counter(str(c.get("category", "unknown")) for c in all_changes)
    where_targets = Counter(str(c.get("where", "unknown")) for c in all_changes)

    # Detect pricing drift direction
    price_changes = [c for c in all_changes if "pric" in str(c.get("what", "")).lower()]
    price_direction = 0
    for pc in price_changes:
        try:
            old_v = float(pc.get("old_value", 0))
            new_v = float(pc.get("new_value", 0))
            if new_v > old_v:
                price_direction += 1
            elif new_v < old_v:
                price_direction -= 1
        except (ValueError, TypeError):
            pass

    # Rollback rate
    rollback_count = sum(1 for c in cycles if c.get("rolled_back"))
    rollback_rate = rollback_count / len(cycles)

    # Compute drift magnitude
    drift_magnitude = 0.0
    details_parts = []

    if abs(price_direction) >= 2:
        direction = "increasing" if price_direction > 0 else "decreasing"
        drift_magnitude += 0.3
        details_parts.append(f"prices consistently {direction} ({abs(price_direction)} changes)")

    if rollback_rate > 0.5:
        drift_magnitude += 0.2
        details_parts.append(f"high rollback rate: {rollback_rate:.0%}")

    # Repetition: same 'where' target hit 3+ times
    for target, count in where_targets.items():
        if count >= 3:
            drift_magnitude += 0.1
            details_parts.append(f"'{target}' modified {count} times")

    alert = drift_magnitude > 0.3
    trend = "drifting" if alert else "stable"

    return {
        "trend": trend,
        "drift_magnitude": min(1.0, drift_magnitude),
        "alert": alert,
        "details": "; ".join(details_parts) if details_parts else "no significant drift",
    }


# ═══════════════════════════════════════════════════════════════
# Convergence Detector (Limits of Self-Improving paper)
# ═══════════════════════════════════════════════════════════════

async def convergence_detector(days: int = 14) -> dict:
    """Detect when the sleep cycle has converged (diminishing returns).

    Measures entropy of proposal categories over time. If entropy is
    decreasing (proposals becoming repetitive/narrow), the system has
    converged and should pause self-modification to consolidate.

    The "Limits of Self-Improving" paper proves entropy decay is inevitable.
    The question is when to detect it and stop.

    Returns: {"converged": bool, "entropy": float, "recommendation": str}
    """
    if not MISALIGNMENT_PROBES_ENABLED:
        return {"converged": False, "entropy": 1.0, "recommendation": "probes disabled"}

    try:
        from shared.db import fetch_all
        cycles = await fetch_all(
            """SELECT applied_changes FROM sleep_cycle_log
               WHERE cycle_date > NOW() - INTERVAL '%s days'
               AND rolled_back = FALSE
               ORDER BY cycle_date ASC""",
            (days,),
        )
    except Exception:
        return {"converged": False, "entropy": 1.0, "recommendation": "db unavailable"}

    if len(cycles) < 3:
        return {"converged": False, "entropy": 1.0, "recommendation": "insufficient data"}

    # Extract proposal categories from each cycle
    all_categories = []
    for cycle in cycles:
        changes = cycle.get("applied_changes", [])
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(changes, list):
            for c in changes:
                all_categories.append(str(c.get("category", "unknown")))

    if not all_categories:
        return {"converged": False, "entropy": 1.0, "recommendation": "no categories"}

    # Compute Shannon entropy
    counter = Counter(all_categories)
    total = len(all_categories)
    entropy = 0.0
    for count in counter.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)

    # Max possible entropy
    max_entropy = math.log2(max(1, len(counter)))
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0

    # Compare first half vs second half entropy
    mid = len(all_categories) // 2
    first_half = all_categories[:mid]
    second_half = all_categories[mid:]

    def half_entropy(cats):
        c = Counter(cats)
        t = len(cats)
        e = 0.0
        for cnt in c.values():
            p = cnt / t
            if p > 0:
                e -= p * math.log2(p)
        return e

    if first_half and second_half:
        e1 = half_entropy(first_half)
        e2 = half_entropy(second_half)
        entropy_declining = e2 < e1 * 0.7  # 30% drop
    else:
        entropy_declining = False

    converged = normalized_entropy < 0.3 or entropy_declining

    if converged:
        recommendation = "Pause self-modification for 1 cycle. Consolidate learnings before continuing."
    else:
        recommendation = "Continue self-modification. Proposal diversity is healthy."

    return {
        "converged": converged,
        "entropy": round(normalized_entropy, 3),
        "recommendation": recommendation,
    }


# ═══════════════════════════════════════════════════════════════
# Forgetting Risk Check (for backprop.py)
# ═══════════════════════════════════════════════════════════════

async def check_forgetting_risk(proposed_edit: dict) -> float:
    """Check if a proposed soul doc edit conflicts with high-confidence learnings.

    Mechanistic Analysis paper: attention disruption causes 15-23% head reorganization.
    FIT paper: forgetting concentrates in later epochs.
    Forget Forgetting: critical params in sparse high-curvature regions.

    Returns risk score 0.0-1.0. If > 0.5, the edit should be blocked.
    """
    if not MISALIGNMENT_PROBES_ENABLED:
        return 0.0

    new_text = str(proposed_edit.get("new_value", proposed_edit.get("new_text", "")))
    if not new_text:
        return 0.0

    # Check against high-confidence titan_rules
    try:
        from shared.db import fetch_all
        rules = await fetch_all(
            """SELECT rule_text, confidence FROM titan_rules
               WHERE active = TRUE AND confidence > 0.7
               ORDER BY confidence DESC LIMIT 20"""
        )
    except Exception:
        return 0.0

    if not rules:
        return 0.0

    # Simple keyword conflict detection
    new_words = set(new_text.lower().split())
    conflict_count = 0

    for rule in rules:
        rule_text = str(rule.get("rule_text", "")).lower()
        rule_words = set(rule_text.split())
        overlap = len(new_words & rule_words)

        # If there's significant keyword overlap but the edit changes behavior,
        # it might be contradicting the rule
        if overlap > 3:
            # Check for negation patterns
            negations = {"not", "never", "don't", "avoid", "stop", "remove", "disable"}
            new_negations = new_words & negations
            rule_negations = rule_words & negations

            if new_negations != rule_negations:
                # Different negation patterns → potential contradiction
                conflict_count += 1
                confidence = rule.get("confidence", 0.5)
                logger.debug(
                    f"Forgetting risk: edit may contradict rule (confidence={confidence:.2f}): "
                    f"{rule_text[:80]}"
                )

    # Risk scales with number of conflicting high-confidence rules
    risk = min(1.0, conflict_count * 0.25)
    return risk
