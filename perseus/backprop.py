"""Backpropagation engine — safely edits behavioral files based on sleep cycle decisions.

The sleep cycle's Alpha/Beta debate produces surviving proposals. This module
applies those proposals to the actual files that determine agent behavior:
- Soul docs (soul/soul_copy.md, soul/soul_agent.md)
- Prompt templates in pipeline stages
- System config values (pricing, targeting)
- Titan rules (activate/deactivate)

Every edit is tracked in agent_decisions with full before/after for rollback.
Immutable sections are protected — compliance rules can never be edited.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any

from shared.comms import record_decision
from shared.config import config
from shared.db import execute, fetch_all, get_config, set_config

logger = logging.getLogger("perseus.backprop")

# ── Immutable protections ─────────────────────────────────────────

# Files that backprop can NEVER touch
IMMUTABLE_FILES: set[str] = {
    "titan/compliance.py",
    "shared/db.py",
    "shared/config.py",
    "scripts/init-db.sql",
}

# Specific line ranges within files that are immutable
# (file_relative_path, start_line, end_line) — 1-indexed, inclusive
IMMUTABLE_SECTIONS: set[tuple[str, int, int]] = {
    ("soul/soul_copy.md", 34, 42),  # Compliance rules — non-negotiable
}

# Pricing guardrails
PRICE_MIN = 149
PRICE_MAX = 499
PRICE_MAX_DELTA_PER_CYCLE = 50

# Maximum proposals per cycle
MAX_PROPOSALS_PER_CYCLE = 5


def _resolve_path(relative_path: str) -> Path:
    """Resolve a relative path against the repo root."""
    return config.root_dir / relative_path


def _is_immutable(file_path: str, start_line: int = 0, end_line: int = 0) -> bool:
    """Check if a file or section is protected from backprop edits."""
    # Normalize path
    rel = file_path
    if str(config.root_dir) in file_path:
        rel = file_path.replace(str(config.root_dir) + "/", "")

    # Full file immutable?
    if rel in IMMUTABLE_FILES:
        return True

    # Any path under tests/
    if rel.startswith("tests/") or rel.startswith("tests\\"):
        return True

    # Section immutable?
    if start_line and end_line:
        for (f, s, e) in IMMUTABLE_SECTIONS:
            if f == rel and start_line <= e and end_line >= s:
                return True

    return False


# ── Edit operations ───────────────────────────────────────────────

async def apply_soul_doc_edit(
    file_path: str,
    old_text: str,
    new_text: str,
    reason: str,
    cycle_id: int,
) -> bool:
    """Edit a soul doc section. Returns True if applied, False if blocked."""
    if _is_immutable(file_path):
        logger.warning(f"Backprop blocked: {file_path} is immutable")
        return False

    full_path = _resolve_path(file_path)
    if not full_path.exists():
        logger.warning(f"Backprop blocked: {file_path} does not exist")
        return False

    content = full_path.read_text()
    if old_text not in content:
        logger.warning(f"Backprop blocked: old_text not found in {file_path}")
        return False

    # Check immutable sections by line number
    lines = content.split("\n")
    old_start = None
    for i, line in enumerate(lines, 1):
        if old_text.split("\n")[0] in line:
            old_start = i
            break
    if old_start:
        old_end = old_start + old_text.count("\n")
        if _is_immutable(file_path, old_start, old_end):
            logger.warning(f"Backprop blocked: lines {old_start}-{old_end} of {file_path} are immutable")
            return False

    # Apply the edit
    new_content = content.replace(old_text, new_text, 1)
    full_path.write_text(new_content)

    # Track
    await record_decision(
        agent="sleep_cycle",
        decision_type="backprop_edit",
        context={"file": file_path, "cycle_id": cycle_id},
        decision={"old": old_text, "new": new_text},
        reasoning=reason,
    )
    logger.info(f"Backprop: edited {file_path} — {reason[:80]}")
    return True


async def apply_config_change(
    key: str,
    new_value: Any,
    reason: str,
    cycle_id: int,
) -> bool:
    """Change a system_config value with pricing guardrails."""
    old_value = await get_config(key)

    # Pricing guardrails
    if "price" in key.lower() or "pricing" in key.lower():
        try:
            old_num = float(old_value or 0)
            new_num = float(new_value)
            if abs(new_num - old_num) > PRICE_MAX_DELTA_PER_CYCLE:
                logger.warning(
                    f"Backprop blocked: price change ${old_num}→${new_num} exceeds "
                    f"max delta of ${PRICE_MAX_DELTA_PER_CYCLE}"
                )
                return False
            if new_num < PRICE_MIN or new_num > PRICE_MAX:
                logger.warning(f"Backprop blocked: price ${new_num} outside [{PRICE_MIN}, {PRICE_MAX}]")
                return False
        except (ValueError, TypeError):
            pass

    await set_config(key, new_value)

    await record_decision(
        agent="sleep_cycle",
        decision_type="backprop_config",
        context={"key": key, "cycle_id": cycle_id},
        decision={"old": old_value, "new": new_value},
        reasoning=reason,
    )
    logger.info(f"Backprop: config {key} = {new_value} — {reason[:80]}")
    return True


async def apply_rule_change(
    rule_id: int,
    active: bool,
    reason: str,
    cycle_id: int,
) -> bool:
    """Activate or deactivate a titan_rule."""
    await execute(
        "UPDATE titan_rules SET active = %s, evaluated_at = NOW() WHERE id = %s",
        (active, rule_id),
    )

    await record_decision(
        agent="sleep_cycle",
        decision_type="backprop_rule",
        context={"rule_id": rule_id, "cycle_id": cycle_id},
        decision={"active": active},
        reasoning=reason,
    )
    logger.info(f"Backprop: rule #{rule_id} {'activated' if active else 'deactivated'} — {reason[:80]}")
    return True


# ── Rollback ──────────────────────────────────────────────────────

async def rollback_cycle(cycle_id: int) -> int:
    """Revert all backprop changes from a sleep cycle. Returns count of reverted changes."""
    changes = await fetch_all(
        """SELECT id, decision_type, context, decision FROM agent_decisions
           WHERE agent = 'sleep_cycle'
           AND context->>'cycle_id' = %s
           ORDER BY created_at DESC""",
        (str(cycle_id),),
    )

    reverted = 0
    for change in changes:
        decision = change.get("decision", {})
        ctx = change.get("context", {})

        if change["decision_type"] == "backprop_edit":
            file_path = ctx.get("file", "")
            full_path = _resolve_path(file_path)
            if full_path.exists():
                content = full_path.read_text()
                new_text = decision.get("new", "")
                old_text = decision.get("old", "")
                if new_text and new_text in content:
                    full_path.write_text(content.replace(new_text, old_text, 1))
                    reverted += 1

        elif change["decision_type"] == "backprop_config":
            key = ctx.get("key", "")
            old_value = decision.get("old")
            if key and old_value is not None:
                await set_config(key, old_value)
                reverted += 1

        elif change["decision_type"] == "backprop_rule":
            rule_id = ctx.get("rule_id")
            old_active = not decision.get("active", True)
            if rule_id:
                await execute(
                    "UPDATE titan_rules SET active = %s WHERE id = %s",
                    (old_active, rule_id),
                )
                reverted += 1

    # ── Cross-store rollback: MAGMA causal edges created during this cycle ──
    magma_reverted = 0
    try:
        from shared.magma import _get_driver
        driver = _get_driver()
        if driver:
            # Find and delete MAGMA nodes + edges ingested during this cycle
            # Nodes are tagged with metadata containing cycle_id or created
            # within the cycle's time window
            cycle_log = await fetch_all(
                "SELECT created_at FROM sleep_cycle_log WHERE id = %s", (cycle_id,)
            )
            if cycle_log:
                cycle_ts = str(cycle_log[0].get("created_at", ""))
                if cycle_ts:
                    with driver.session() as session:
                        # Delete causal edges inferred during this cycle
                        result = session.run(
                            """MATCH ()-[r:CAUSED]->()
                               WHERE r.inferred_at >= $ts
                               DELETE r
                               RETURN count(r) as deleted""",
                            ts=cycle_ts,
                        ).single()
                        magma_reverted = result["deleted"] if result else 0
    except Exception as e:
        logger.debug(f"MAGMA rollback failed (non-critical): {e}")

    # ── Cross-store rollback: Mem0 memories stored during reflection ──
    mem0_reverted = 0
    try:
        # Delete titan_learnings created during this cycle (by the reflection
        # that ran just before it) — they may contain insights that led to
        # the bad proposals we're reverting
        reflection_rows = await fetch_all(
            """SELECT id FROM titan_learnings
               WHERE category = 'daily_reflection'
               AND created_at >= (SELECT created_at FROM sleep_cycle_log WHERE id = %s)
               AND created_at <= (SELECT created_at FROM sleep_cycle_log WHERE id = %s) + INTERVAL '1 hour'""",
            (cycle_id, cycle_id),
        )
        if reflection_rows:
            for row in reflection_rows:
                await execute("DELETE FROM titan_learnings WHERE id = %s", (row["id"],))
                mem0_reverted += 1
    except Exception as e:
        logger.debug(f"Learnings rollback failed (non-critical): {e}")

    total_reverted = reverted + magma_reverted + mem0_reverted
    if total_reverted:
        await execute(
            "UPDATE sleep_cycle_log SET rolled_back = TRUE WHERE id = %s",
            (cycle_id,),
        )
        logger.info(
            f"Rolled back sleep cycle #{cycle_id}: "
            f"{reverted} backprop changes, {magma_reverted} MAGMA edges, "
            f"{mem0_reverted} learnings"
        )

        # Emit event so Hermes alerts the operator
        from shared.db import emit_event
        await emit_event("sleep_cycle_rolled_back", {
            "cycle_id": cycle_id,
            "backprop_reverted": reverted,
            "magma_edges_reverted": magma_reverted,
            "learnings_reverted": mem0_reverted,
        })

    return total_reverted


# ── Git integration ───────────────────────────────────────────────

async def git_commit_cycle(cycle_id: int, summary: str) -> bool:
    """Commit all backprop changes as a single git commit."""
    try:
        result = subprocess.run(
            ["git", "add", "soul/", "titan/pipeline/", "hermes/"],
            capture_output=True,
            text=True,
            cwd=str(config.root_dir),
        )
        if result.returncode != 0:
            return False

        msg = f"sleep cycle #{cycle_id}: {summary[:200]}"
        result = subprocess.run(
            ["git", "commit", "-m", msg, "--allow-empty"],
            capture_output=True,
            text=True,
            cwd=str(config.root_dir),
        )
        return result.returncode == 0

    except Exception as e:
        logger.debug(f"Git commit failed (non-critical): {e}")
        return False
