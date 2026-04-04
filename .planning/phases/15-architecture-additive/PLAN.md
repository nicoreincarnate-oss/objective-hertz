# Phase 15: Architecture — Additive Patterns — PLAN

**Goal:** Implement 3 Paperclip architecture patterns that ADD new capabilities without modifying existing control flow — goal cascade hierarchy, governance/approval system, and commit metrics tracker.
**Requirements:** AP-12 through AP-14
**Depends on:** Phases 11-14 complete (budget consolidation, foundation patterns, budget/cost patterns, quality/observability).
**Feature flags:** All default OFF. Enable individually as each pattern passes tests.
**License:** MIT (Paperclip source)

---

## Context

### Current State

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| DB helpers | `shared/db.py` | 235 | Pool, insert_task (with depth param from Phase 12), emit_event, get/set_config, transaction() |
| task_queue table | `scripts/init-db.sql` | id, task_type, payload, status, priority, assigned_agent, depth, retry_count, created_at, started_at, completed_at, error, result |
| agent_registry table | `scripts/init-db.sql` | name (PK), description, status, state, pause_reason, last_heartbeat, updated_at |
| Decision audit trail | `openjarvis/core/decisions.py` | 200 | Existing decision logging |
| Hermes dashboard | `hermes/web/app.py` | 1340 | FastAPI with 30+ routes, auth, operator chat |
| system_config table | `scripts/init-db.sql` | JSONB key-value store |
| Perseus scheduler | `perseus/scheduler.py` | 59 | Schedule dataclass, SCHEDULES list (26 tasks) |
| Latest migration | `scripts/migrations/024-neuro-scores.sql` | (025-027 reserved by Phases 12-14) |
| No existing approval/governance system | — | — | — |

### What Phase 15 Adds (Pure Additive)

1. **Goal Cascade** — hierarchical goal tree (company → team → agent → task) with progress aggregation
2. **Governance / Approval System** — approval workflow for sensitive actions (review_mode, budget, capabilities)
3. **Commit Metrics Tracker** — daily git log collection and agent attribution via Co-Authored-By

None of these modify existing control flow. They add new tables, new files, and new API routes.

---

## Feature Flags

| Flag | Env Var | Default | Pattern |
|------|---------|---------|---------|
| Goal Cascade | `GOAL_CASCADE_ENABLED` | `false` | 12 |
| Governance | `GOVERNANCE_ENABLED` | `false` | 13 |
| Commit Metrics | `COMMIT_METRICS_ENABLED` | `false` | 14 |

All flags checked via `os.environ.get(flag, "").lower() in ("true", "1")` — consistent with existing pattern in `shared/agent_base.py`.

---

## Tasks

### Plan 15-01: Goal Cascade (AP-12)

Source: Paperclip `schema/goals.ts` + `services/issue-goal-fallback.ts` (MIT)

#### Task 1A: Create goals table and seed data
**File:** `scripts/migrations/028-architecture-additive.sql` (see Migration section below)
**Action:** CREATE TABLE goals, ALTER TABLE task_queue ADD goal_tag, INSERT seed goals.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS goals (
    goal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level VARCHAR(20) NOT NULL CHECK (level IN ('company', 'team', 'agent', 'task')),
    parent_id UUID REFERENCES goals(goal_id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    success_criteria TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'failed')),
    assigned_agent VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Seed goals:**
```sql
-- Company-level goal
INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'company', NULL,
    'Generate $2K MRR from autonomous client acquisition',
    'Monthly recurring revenue >= $2000 from active client subscriptions',
    'active'
);

-- Team-level goals (children of company goal)
INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES
    ('a0000000-0000-0000-0000-000000000010', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Build 10 client websites with full pipeline delivery', '10 sites deployed and verified live', 'active'),
    ('a0000000-0000-0000-0000-000000000011', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Maintain 95% pipeline task completion rate', 'task_completion_rate >= 0.95 over rolling 30 days', 'active'),
    ('a0000000-0000-0000-0000-000000000012', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Achieve <60s end-to-end pipeline latency', 'p95 latency < 60000ms measured by observability', 'active');

-- Agent-level goals (children of team goals)
INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, assigned_agent, status)
VALUES
    ('a0000000-0000-0000-0000-000000000100', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Discover and qualify 50 leads per week', '50+ leads with status >= researched per 7-day window',
     'titan', 'active'),
    ('a0000000-0000-0000-0000-000000000101', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Build and deploy client sites within 2h of deal close', 'site_deploy_time < 7200s from close event',
     'clawdbot', 'active'),
    ('a0000000-0000-0000-0000-000000000102', 'agent', 'a0000000-0000-0000-0000-000000000011',
     'Process all scheduled tasks within interval ceiling', 'zero skipped non-skippable tasks per 24h',
     'perseus', 'active'),
    ('a0000000-0000-0000-0000-000000000103', 'agent', 'a0000000-0000-0000-0000-000000000012',
     'Deliver alerts within 5 seconds of event', 'p99 alert_delivery_latency < 5000ms',
     'hermes', 'active');
```

#### Task 1B: Create goal_cascade module
**File:** `shared/goal_cascade.py` (new, ~200 lines)
**Action:** Implement goal CRUD, cascade resolution, progress aggregation, and tree retrieval.

```python
"""Goal Cascade — hierarchical goal tree with cascade resolution.

Source: Paperclip schema/goals.ts + services/issue-goal-fallback.ts (MIT)
Feature flag: GOAL_CASCADE_ENABLED

Levels: company → team → agent → task
Tasks auto-inherit the company goal when no explicit goal_tag is set.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from shared import db

logger = logging.getLogger("shared.goal_cascade")


def _enabled() -> bool:
    return os.environ.get("GOAL_CASCADE_ENABLED", "").lower() in ("true", "1")


async def create_goal(
    level: str,
    description: str,
    parent_id: str | None = None,
    success_criteria: str | None = None,
    assigned_agent: str | None = None,
) -> str | None:
    """Create a goal. Returns goal_id UUID string, or None if flag is off.

    When parent_id is provided, inherits level validation:
    company goals have no parent, team goals must have company parent, etc.
    """
    if not _enabled():
        return None

    valid_levels = ("company", "team", "agent", "task")
    if level not in valid_levels:
        raise ValueError(f"Invalid goal level: {level}. Must be one of {valid_levels}")

    goal_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, assigned_agent)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (goal_id, level, parent_id, description, success_criteria, assigned_agent),
    )
    logger.info("Created %s goal %s: %s", level, goal_id[:8], description[:80])
    return goal_id


async def resolve_goal(task_type: str, assigned_agent: str | None = None) -> dict[str, Any] | None:
    """Find applicable goal by cascade: task → agent → team → company.

    Resolution order:
    1. Task-level goal matching task_type
    2. Agent-level goal matching assigned_agent
    3. First active team-level goal
    4. First active company-level goal (always exists as fallback)

    Returns goal dict or None if flag is off / no goals exist.
    """
    if not _enabled():
        return None

    # 1. Task-level: match by description containing task_type
    task_goal = await db.fetch_one(
        """SELECT * FROM goals
           WHERE level = 'task' AND status = 'active'
             AND description ILIKE %s
           ORDER BY created_at DESC LIMIT 1""",
        (f"%{task_type}%",),
    )
    if task_goal:
        return task_goal

    # 2. Agent-level: match by assigned_agent
    if assigned_agent:
        agent_goal = await db.fetch_one(
            """SELECT * FROM goals
               WHERE level = 'agent' AND status = 'active'
                 AND assigned_agent = %s
               ORDER BY created_at DESC LIMIT 1""",
            (assigned_agent,),
        )
        if agent_goal:
            return agent_goal

    # 3. Team-level fallback
    team_goal = await db.fetch_one(
        """SELECT * FROM goals
           WHERE level = 'team' AND status = 'active'
           ORDER BY created_at ASC LIMIT 1""",
    )
    if team_goal:
        return team_goal

    # 4. Company-level fallback
    company_goal = await db.fetch_one(
        """SELECT * FROM goals
           WHERE level = 'company' AND status = 'active'
           ORDER BY created_at ASC LIMIT 1""",
    )
    return company_goal


async def update_goal_progress(goal_id: str) -> dict[str, Any]:
    """Aggregate child goal completion percentage for a parent goal.

    Returns dict with:
    - total_children: int
    - completed_children: int
    - completion_pct: float (0.0 - 100.0)
    - child_statuses: dict[str, int] (status → count)
    """
    rows = await db.fetch_all(
        """SELECT status, COUNT(*) as cnt
           FROM goals
           WHERE parent_id = %s
           GROUP BY status""",
        (goal_id,),
    )

    child_statuses: dict[str, int] = {}
    total = 0
    completed = 0
    for row in rows:
        status = row["status"]
        count = row["cnt"]
        child_statuses[status] = count
        total += count
        if status == "completed":
            completed += count

    completion_pct = (completed / total * 100.0) if total > 0 else 0.0

    # Update parent's updated_at timestamp
    await db.execute(
        "UPDATE goals SET updated_at = NOW() WHERE goal_id = %s",
        (goal_id,),
    )

    return {
        "goal_id": goal_id,
        "total_children": total,
        "completed_children": completed,
        "completion_pct": round(completion_pct, 1),
        "child_statuses": child_statuses,
    }


async def get_goal_tree() -> list[dict[str, Any]]:
    """Return full goal hierarchy as nested tree for dashboard visualization.

    Returns list of company-level goals, each with nested 'children' arrays
    recursively populated via a single recursive CTE query.
    """
    if not _enabled():
        return []

    # Fetch all goals, build tree in Python (simpler than recursive CTE for 4 levels)
    all_goals = await db.fetch_all(
        """SELECT goal_id, level, parent_id, description, success_criteria,
                  status, assigned_agent, created_at, updated_at
           FROM goals
           ORDER BY level, created_at ASC""",
    )

    # Build lookup and tree
    by_id: dict[str, dict] = {}
    for g in all_goals:
        g["children"] = []
        by_id[str(g["goal_id"])] = g

    roots: list[dict] = []
    for g in all_goals:
        parent = str(g["parent_id"]) if g.get("parent_id") else None
        if parent and parent in by_id:
            by_id[parent]["children"].append(g)
        else:
            roots.append(g)

    return roots


async def complete_goal(goal_id: str) -> bool:
    """Mark a goal as completed. Returns True if updated."""
    if not _enabled():
        return False
    result = await db.fetch_one(
        """UPDATE goals SET status = 'completed', updated_at = NOW()
           WHERE goal_id = %s AND status = 'active'
           RETURNING goal_id""",
        (goal_id,),
    )
    if result:
        logger.info("Goal %s completed", goal_id[:8])
    return result is not None


async def fail_goal(goal_id: str) -> bool:
    """Mark a goal as failed. Returns True if updated."""
    if not _enabled():
        return False
    result = await db.fetch_one(
        """UPDATE goals SET status = 'failed', updated_at = NOW()
           WHERE goal_id = %s AND status = 'active'
           RETURNING goal_id""",
        (goal_id,),
    )
    if result:
        logger.warning("Goal %s failed", goal_id[:8])
    return result is not None
```

#### Task 1C: Add goal_tag to task insertion
**File:** `shared/db.py`
**Action:** Add optional `goal_tag: str | None = None` parameter to `insert_task()`.

**Details:**
1. Modify `insert_task()` signature (line 155) to accept `goal_tag: str | None = None`
2. Modify the INSERT query (line 180) to include goal_tag:
   ```python
   row = await fetch_one(
       """INSERT INTO task_queue (task_type, payload, priority, depth, goal_tag)
          VALUES (%s, %s, %s, %s, %s) RETURNING id""",
       (task_type, json.dumps(payload or {}), priority, depth, goal_tag),
   )
   ```
3. When `goal_tag` is None and `GOAL_CASCADE_ENABLED` is true, auto-resolve:
   ```python
   if goal_tag is None and os.environ.get("GOAL_CASCADE_ENABLED", "").lower() in ("true", "1"):
       from shared.goal_cascade import resolve_goal
       resolved = await resolve_goal(task_type)
       if resolved:
           goal_tag = str(resolved["goal_id"])[:8]  # Short tag, not full UUID
   ```

**Note:** `goal_tag` is VARCHAR, NOT a foreign key. Lightweight tag for grouping/filtering only.

#### Task 1D: Hermes dashboard API for goal tree
**File:** `hermes/web/app.py`
**Action:** Add `GET /api/goals/tree` endpoint.

**Details:**
Add after the `/api/decisions` route (line 1215):
```python
@app.get("/api/goals/tree")
async def get_goals_tree():
    """Return the full goal hierarchy for dashboard visualization."""
    try:
        from shared.goal_cascade import get_goal_tree, _enabled
        if not _enabled():
            return {"goals": [], "enabled": False}
        tree = await get_goal_tree()
        return {"goals": tree, "enabled": True}
    except Exception as e:
        logger.error("Goal tree fetch failed: %s", e)
        return {"goals": [], "enabled": False, "error": str(e)}
```

---

### Plan 15-02: Governance / Approval System (AP-13)

Source: Paperclip `schema/approvals.ts` (MIT) — adapted for flat operator model (no org hierarchy).

#### Task 2A: Create approvals table
**File:** `scripts/migrations/028-architecture-additive.sql` (see Migration section)
**Action:** CREATE TABLE approvals with type, status, expiry tracking.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(40) NOT NULL CHECK (type IN (
        'budget_override', 'autonomy_transition', 'config_change', 'capability_grant'
    )),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'approved', 'rejected', 'expired'
    )),
    requested_by VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    resolved_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### Task 2B: Create governance module
**File:** `shared/governance.py` (new, ~180 lines)
**Action:** Implement approval request/resolve, policy check, and auto-expiry.

```python
"""Governance / Approval System — approval workflow for sensitive actions.

Source: Paperclip schema/approvals.ts (MIT)
Feature flag: GOVERNANCE_ENABLED

Approval types:
- budget_override: spending above configured threshold
- autonomy_transition: review_mode True→False (AEGIS finding)
- config_change: system_config modifications to protected keys
- capability_grant: new daemon capabilities or tool access
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from shared import db

logger = logging.getLogger("shared.governance")

# Keys in system_config that require approval to change
PROTECTED_CONFIG_KEYS = frozenset({
    "review_mode",
    "max_daily_spend_usd",
    "max_task_depth",
    "enable_middleware",
    "email_send_enabled",
})

# Action types that always require approval
APPROVAL_REQUIRED_ACTIONS = frozenset({
    "autonomy_transition",
    "budget_override",
    "capability_grant",
})


def _enabled() -> bool:
    return os.environ.get("GOVERNANCE_ENABLED", "").lower() in ("true", "1")


async def request_approval(
    approval_type: str,
    details: dict[str, Any],
    requested_by: str,
    expires_hours: int = 24,
) -> str | None:
    """Create a pending approval request. Returns approval_id or None if flag is off.

    Also emits an event for Hermes to send Telegram notification.
    """
    if not _enabled():
        return None

    valid_types = ("budget_override", "autonomy_transition", "config_change", "capability_grant")
    if approval_type not in valid_types:
        raise ValueError(f"Invalid approval type: {approval_type}. Must be one of {valid_types}")

    approval_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO approvals (approval_id, type, status, requested_by, details, expires_at)
           VALUES (%s, %s, 'pending', %s, %s, NOW() + make_interval(hours => %s))""",
        (approval_id, approval_type, requested_by, Jsonb(details), expires_hours),
    )

    # Emit event for Hermes alert dispatch
    await db.emit_event("approval_requested", {
        "approval_id": approval_id,
        "type": approval_type,
        "requested_by": requested_by,
        "details": details,
    })

    logger.info(
        "Approval requested: %s by %s (id=%s, expires=%dh)",
        approval_type, requested_by, approval_id[:8], expires_hours,
    )
    return approval_id


async def resolve_approval(
    approval_id: str,
    decision: str,
    resolved_by: str,
) -> bool:
    """Approve or reject a pending approval. Returns True if resolved.

    decision must be 'approved' or 'rejected'.
    """
    if not _enabled():
        return False

    if decision not in ("approved", "rejected"):
        raise ValueError(f"Invalid decision: {decision}. Must be 'approved' or 'rejected'")

    result = await db.fetch_one(
        """UPDATE approvals
           SET status = %s, resolved_by = %s, resolved_at = NOW()
           WHERE approval_id = %s AND status = 'pending'
           RETURNING approval_id""",
        (decision, resolved_by, approval_id),
    )

    if result:
        await db.emit_event("approval_resolved", {
            "approval_id": approval_id,
            "decision": decision,
            "resolved_by": resolved_by,
        })
        logger.info("Approval %s %s by %s", approval_id[:8], decision, resolved_by)

    return result is not None


async def check_approval_required(action_type: str, config_key: str | None = None) -> bool:
    """Check if an action requires approval per policy.

    Returns True if approval is needed, False if the action can proceed.
    Always returns False when GOVERNANCE_ENABLED is off.
    """
    if not _enabled():
        return False

    # Always-require types
    if action_type in APPROVAL_REQUIRED_ACTIONS:
        return True

    # Config changes to protected keys
    if action_type == "config_change" and config_key in PROTECTED_CONFIG_KEYS:
        return True

    return False


async def check_pending_approval(approval_type: str, requested_by: str) -> dict[str, Any] | None:
    """Check if there's already a pending approval of this type from this requester.

    Prevents duplicate approval requests.
    """
    if not _enabled():
        return None

    return await db.fetch_one(
        """SELECT * FROM approvals
           WHERE type = %s AND requested_by = %s AND status = 'pending'
             AND expires_at > NOW()
           ORDER BY created_at DESC LIMIT 1""",
        (approval_type, requested_by),
    )


async def get_approved(approval_id: str) -> bool:
    """Check if a specific approval has been granted.

    Returns True only if status is 'approved'.
    """
    if not _enabled():
        return True  # When governance is off, everything is auto-approved

    row = await db.fetch_one(
        "SELECT status FROM approvals WHERE approval_id = %s",
        (approval_id,),
    )
    return row is not None and row["status"] == "approved"


async def auto_expire_stale() -> int:
    """Expire pending approvals older than their expires_at. Returns count expired.

    Called by Perseus scheduler daily.
    """
    if not _enabled():
        return 0

    result = await db.fetch_all(
        """UPDATE approvals
           SET status = 'expired'
           WHERE status = 'pending' AND expires_at < NOW()
           RETURNING approval_id""",
    )
    count = len(result)
    if count > 0:
        logger.info("Auto-expired %d stale approvals", count)
    return count


async def get_pending_approvals() -> list[dict[str, Any]]:
    """Get all pending (non-expired) approvals for dashboard display."""
    if not _enabled():
        return []

    return await db.fetch_all(
        """SELECT approval_id, type, status, requested_by, details,
                  expires_at, created_at
           FROM approvals
           WHERE status = 'pending' AND expires_at > NOW()
           ORDER BY created_at DESC""",
    )


async def get_approval_history(limit: int = 50) -> list[dict[str, Any]]:
    """Get recent approval history (all statuses) for audit trail."""
    if not _enabled():
        return []

    return await db.fetch_all(
        """SELECT approval_id, type, status, requested_by, details,
                  resolved_by, resolved_at, expires_at, created_at
           FROM approvals
           ORDER BY created_at DESC
           LIMIT %s""",
        (limit,),
    )
```

#### Task 2C: Wire governance into review_mode transition (AEGIS fix)
**File:** `hermes/web/app.py`
**Action:** Modify `POST /api/config` to require approval for review_mode changes.

**Details:**
In the `/api/config` POST handler (line 884), add approval gate for `review_mode`:
```python
@app.post("/api/config")
async def update_config(request: Request):
    data = await request.json()
    key = data.get("key")
    value = data.get("value")

    # Governance check: protected keys require approval
    try:
        from shared.governance import check_approval_required, request_approval
        if await check_approval_required("config_change", config_key=key):
            # Check if review_mode transition True→False (AEGIS finding)
            if key == "review_mode":
                current = await db.get_config("review_mode", True)
                if current is True and value is False:
                    approval_id = await request_approval(
                        "autonomy_transition",
                        {"key": key, "from": current, "to": value},
                        requested_by="operator",
                    )
                    if approval_id:
                        return {"status": "approval_required", "approval_id": approval_id,
                                "message": "review_mode transition requires operator confirmation via Telegram"}
            else:
                approval_id = await request_approval(
                    "config_change",
                    {"key": key, "value": value},
                    requested_by="operator",
                )
                if approval_id:
                    return {"status": "approval_required", "approval_id": approval_id}
    except ImportError:
        pass  # governance module not available

    # Existing config update logic continues here...
```

#### Task 2D: Hermes dashboard API for approvals
**File:** `hermes/web/app.py`
**Action:** Add `GET /api/approvals` and `POST /api/approvals/{id}/resolve` endpoints.

**Details:**
Add after the `/api/goals/tree` route:
```python
@app.get("/api/approvals")
async def get_approvals():
    """Return pending approvals and recent history for operator review."""
    try:
        from shared.governance import get_pending_approvals, get_approval_history, _enabled
        if not _enabled():
            return {"pending": [], "history": [], "enabled": False}
        pending = await get_pending_approvals()
        history = await get_approval_history(limit=20)
        return {"pending": pending, "history": history, "enabled": True}
    except Exception as e:
        logger.error("Approvals fetch failed: %s", e)
        return {"pending": [], "history": [], "enabled": False, "error": str(e)}


@app.post("/api/approvals/{approval_id}/resolve")
async def resolve_approval_endpoint(approval_id: str, request: Request):
    """Approve or reject a pending approval."""
    try:
        from shared.governance import resolve_approval
        data = await request.json()
        decision = data.get("decision")  # "approved" or "rejected"
        resolved_by = data.get("resolved_by", "operator")

        if decision not in ("approved", "rejected"):
            return {"error": "decision must be 'approved' or 'rejected'"}, 400

        success = await resolve_approval(approval_id, decision, resolved_by)
        if not success:
            return {"error": "Approval not found or already resolved"}, 404
        return {"status": "ok", "approval_id": approval_id, "decision": decision}
    except Exception as e:
        logger.error("Approval resolve failed: %s", e)
        return {"error": str(e)}, 500
```

#### Task 2E: Wire Hermes Telegram notification for new approvals
**File:** `hermes/alerts.py`
**Action:** Add handler for `approval_requested` event type.

**Details:**
In the alert dispatch logic, add a handler for the `approval_requested` event:
```python
async def handle_approval_requested(event: dict) -> None:
    """Send Telegram notification when a new approval is requested."""
    details = event.get("payload", {})
    approval_type = details.get("type", "unknown")
    requested_by = details.get("requested_by", "unknown")
    approval_details = details.get("details", {})

    message = (
        f"🔐 *Approval Required*\n\n"
        f"Type: `{approval_type}`\n"
        f"Requested by: `{requested_by}`\n"
        f"Details: `{str(approval_details)[:200]}`\n\n"
        f"Review at: /api/approvals"
    )
    await send_telegram_alert(message, parse_mode="Markdown")
```

Register in the event handler map alongside existing alert types.

#### Task 2F: Perseus scheduler task for auto-expiry
**File:** `perseus/scheduler.py`
**Action:** Add `expire_stale_approvals` scheduled task.

**Details:**
Add to `SCHEDULES` list after the infrastructure section:
```python
# Governance (Phase 15)
Schedule("expire_stale_approvals", 3600, "Auto-expire pending approvals >24h old", skippable=False),
```

The task handler in the daemon calls:
```python
from shared.governance import auto_expire_stale
expired_count = await auto_expire_stale()
```

---

### Plan 15-03: Commit Metrics Tracker (AP-14)

Source: Paperclip `scripts/paperclip-commit-metrics.ts` (MIT)

#### Task 3A: Create commit_metrics table
**File:** `scripts/migrations/028-architecture-additive.sql` (see Migration section)
**Action:** CREATE TABLE commit_metrics.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS commit_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commit_sha VARCHAR(40) UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    files_changed INTEGER NOT NULL DEFAULT 0,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_removed INTEGER NOT NULL DEFAULT 0,
    co_authored BOOLEAN NOT NULL DEFAULT false,
    agent_id VARCHAR(100),
    session_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### Task 3B: Create commit metrics module
**File:** `tools/commit_metrics.py` (new, ~120 lines)
**Action:** Implement git log parsing, DB recording, and metrics aggregation.

```python
"""Commit Metrics Tracker — collect and attribute git commit statistics.

Source: Paperclip scripts/paperclip-commit-metrics.ts (MIT)
Feature flag: COMMIT_METRICS_ENABLED

Standalone module — NOT wired to Conway (Conway has P0 bugs).
Detects Co-Authored-By headers to attribute agent work.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

from shared import db

logger = logging.getLogger("tools.commit_metrics")

# Pattern: Co-Authored-By: Name <email>
_CO_AUTHOR_PATTERN = re.compile(
    r"Co-Authored-By:\s*(.+?)\s*<([^>]+)>", re.IGNORECASE
)

# Known agent identifiers in Co-Authored-By headers
_AGENT_IDENTIFIERS = {
    "claude": "claude",
    "anthropic": "claude",
    "perseus": "perseus",
    "titan": "titan",
    "hermes": "hermes",
    "clawdbot": "clawdbot",
    "ruflo": "ruflo",
}


def _enabled() -> bool:
    return os.environ.get("COMMIT_METRICS_ENABLED", "").lower() in ("true", "1")


def _detect_agent(co_author_name: str, co_author_email: str) -> str | None:
    """Detect agent identity from Co-Authored-By header."""
    combined = f"{co_author_name} {co_author_email}".lower()
    for keyword, agent_id in _AGENT_IDENTIFIERS.items():
        if keyword in combined:
            return agent_id
    return None


def collect_recent_commits(since_hours: int = 24, repo_path: str = ".") -> list[dict[str, Any]]:
    """Parse git log for commits in the last N hours.

    Returns list of dicts with: sha, timestamp, files_changed, lines_added,
    lines_removed, co_authored, agent_id, message.

    Uses subprocess with shell=False (AEGIS compliant).
    """
    since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()

    try:
        # Get commit SHAs and timestamps
        result = subprocess.run(
            ["git", "log", f"--since={since_date}", "--format=%H|%aI|%B---END---", "--numstat"],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("git log failed: %s", result.stderr[:200])
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git log error: %s", exc)
        return []

    commits: list[dict[str, Any]] = []
    # Parse with simpler per-commit approach
    raw_commits = result.stdout.split("---END---")

    for block in raw_commits:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if not lines or "|" not in lines[0]:
            continue

        header_parts = lines[0].split("|", 2)
        if len(header_parts) < 2:
            continue

        sha = header_parts[0].strip()
        timestamp_str = header_parts[1].strip()
        message = header_parts[2].strip() if len(header_parts) > 2 else ""

        # Parse numstat lines (added\tremoved\tfile)
        files_changed = 0
        lines_added = 0
        lines_removed = 0
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) == 3:
                try:
                    added = int(parts[0]) if parts[0] != "-" else 0
                    removed = int(parts[1]) if parts[1] != "-" else 0
                    lines_added += added
                    lines_removed += removed
                    files_changed += 1
                except ValueError:
                    continue

        # Detect co-authoring
        co_author_match = _CO_AUTHOR_PATTERN.search(message)
        co_authored = co_author_match is not None
        agent_id = None
        if co_author_match:
            agent_id = _detect_agent(co_author_match.group(1), co_author_match.group(2))

        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            timestamp = datetime.now(timezone.utc)

        commits.append({
            "sha": sha[:40],
            "timestamp": timestamp,
            "files_changed": files_changed,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "co_authored": co_authored,
            "agent_id": agent_id,
            "message": message[:500],
        })

    return commits


async def record_commit_metrics(commit: dict[str, Any], session_id: str | None = None) -> bool:
    """Write a single commit's metrics to DB. Returns True if inserted (not duplicate)."""
    try:
        await db.execute(
            """INSERT INTO commit_metrics
                   (commit_sha, timestamp, files_changed, lines_added, lines_removed,
                    co_authored, agent_id, session_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (commit_sha) DO NOTHING""",
            (
                commit["sha"],
                commit["timestamp"],
                commit["files_changed"],
                commit["lines_added"],
                commit["lines_removed"],
                commit["co_authored"],
                commit.get("agent_id"),
                session_id,
            ),
        )
        return True
    except Exception as exc:
        logger.debug("Commit metric insert failed for %s: %s", commit["sha"][:8], exc)
        return False


async def collect_and_record(since_hours: int = 24, repo_path: str = ".") -> int:
    """Full pipeline: collect recent commits and record to DB. Returns count recorded."""
    if not _enabled():
        return 0

    commits = collect_recent_commits(since_hours=since_hours, repo_path=repo_path)
    recorded = 0
    for commit in commits:
        if await record_commit_metrics(commit):
            recorded += 1

    if recorded > 0:
        logger.info("Recorded %d/%d commit metrics (last %dh)", recorded, len(commits), since_hours)
    return recorded


async def get_metrics_summary(days: int = 30) -> dict[str, Any]:
    """Aggregate commit metrics for dashboard display.

    Returns:
    - total_commits: int
    - total_files_changed: int
    - total_lines_added: int
    - total_lines_removed: int
    - co_authored_count: int
    - co_authored_pct: float
    - by_agent: dict[str, int] (agent_id → commit count)
    - daily_activity: list[dict] (date → commit count for charting)
    """
    if not _enabled():
        return {"enabled": False}

    summary_row = await db.fetch_one(
        """SELECT
               COUNT(*) as total_commits,
               COALESCE(SUM(files_changed), 0) as total_files_changed,
               COALESCE(SUM(lines_added), 0) as total_lines_added,
               COALESCE(SUM(lines_removed), 0) as total_lines_removed,
               COUNT(*) FILTER (WHERE co_authored = true) as co_authored_count
           FROM commit_metrics
           WHERE timestamp > NOW() - INTERVAL '%s days'""",
        (days,),
    )

    total = summary_row["total_commits"] if summary_row else 0
    co_authored = summary_row["co_authored_count"] if summary_row else 0

    # By agent breakdown
    agent_rows = await db.fetch_all(
        """SELECT COALESCE(agent_id, 'human') as agent, COUNT(*) as cnt
           FROM commit_metrics
           WHERE timestamp > NOW() - INTERVAL '%s days'
           GROUP BY agent_id
           ORDER BY cnt DESC""",
        (days,),
    )
    by_agent = {row["agent"]: row["cnt"] for row in agent_rows}

    # Daily activity for charting
    daily_rows = await db.fetch_all(
        """SELECT DATE(timestamp) as day, COUNT(*) as cnt
           FROM commit_metrics
           WHERE timestamp > NOW() - INTERVAL '%s days'
           GROUP BY DATE(timestamp)
           ORDER BY day ASC""",
        (days,),
    )
    daily_activity = [{"date": str(row["day"]), "commits": row["cnt"]} for row in daily_rows]

    return {
        "enabled": True,
        "days": days,
        "total_commits": total,
        "total_files_changed": summary_row["total_files_changed"] if summary_row else 0,
        "total_lines_added": summary_row["total_lines_added"] if summary_row else 0,
        "total_lines_removed": summary_row["total_lines_removed"] if summary_row else 0,
        "co_authored_count": co_authored,
        "co_authored_pct": round((co_authored / total * 100) if total > 0 else 0, 1),
        "by_agent": by_agent,
        "daily_activity": daily_activity,
    }
```

#### Task 3C: Perseus scheduler task for daily collection
**File:** `perseus/scheduler.py`
**Action:** Add `collect_commit_metrics` scheduled task (non-skippable).

**Details:**
Add to `SCHEDULES` list in the infrastructure section:
```python
# Commit metrics (Phase 15)
Schedule("collect_commit_metrics", 86400, "Daily git commit metrics collection", skippable=False),
```

The task handler in the daemon calls:
```python
from tools.commit_metrics import collect_and_record
count = await collect_and_record(since_hours=24)
```

#### Task 3D: Hermes dashboard API for commit metrics
**File:** `hermes/web/app.py`
**Action:** Add `GET /api/commit-metrics` endpoint.

**Details:**
Add after the approvals routes:
```python
@app.get("/api/commit-metrics")
async def get_commit_metrics(days: int = 30):
    """Return commit metrics summary for dashboard."""
    try:
        from tools.commit_metrics import get_metrics_summary
        summary = await get_metrics_summary(days=days)
        return summary
    except Exception as e:
        logger.error("Commit metrics fetch failed: %s", e)
        return {"enabled": False, "error": str(e)}
```

---

## Database Migration

### Migration 028: Architecture Additive Patterns

**File:** `scripts/migrations/028-architecture-additive.sql`

```sql
-- Migration 028: Architecture Additive Patterns (Phase 15)
-- Three new tables + one ALTER. All additive — no existing tables modified beyond adding a column.
-- Safe for hot system with zero downtime.

BEGIN;

-- ============================================================
-- Pattern 12: Goal Cascade
-- ============================================================

CREATE TABLE IF NOT EXISTS goals (
    goal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level VARCHAR(20) NOT NULL CHECK (level IN ('company', 'team', 'agent', 'task')),
    parent_id UUID REFERENCES goals(goal_id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    success_criteria TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'failed')),
    assigned_agent VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for goal queries
CREATE INDEX IF NOT EXISTS idx_goals_level ON goals(level);
CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_agent ON goals(assigned_agent);

-- Lightweight goal tag on task_queue (NOT a foreign key)
ALTER TABLE task_queue
    ADD COLUMN IF NOT EXISTS goal_tag VARCHAR DEFAULT NULL;

-- Seed goals: company → team → agent hierarchy
INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'company', NULL,
    'Generate $2K MRR from autonomous client acquisition',
    'Monthly recurring revenue >= $2000 from active client subscriptions',
    'active'
) ON CONFLICT DO NOTHING;

INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES
    ('a0000000-0000-0000-0000-000000000010', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Build 10 client websites with full pipeline delivery',
     '10 sites deployed and verified live', 'active'),
    ('a0000000-0000-0000-0000-000000000011', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Maintain 95% pipeline task completion rate',
     'task_completion_rate >= 0.95 over rolling 30 days', 'active'),
    ('a0000000-0000-0000-0000-000000000012', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Achieve <60s end-to-end pipeline latency',
     'p95 latency < 60000ms measured by observability', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, assigned_agent, status)
VALUES
    ('a0000000-0000-0000-0000-000000000100', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Discover and qualify 50 leads per week',
     '50+ leads with status >= researched per 7-day window', 'titan', 'active'),
    ('a0000000-0000-0000-0000-000000000101', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Build and deploy client sites within 2h of deal close',
     'site_deploy_time < 7200s from close event', 'clawdbot', 'active'),
    ('a0000000-0000-0000-0000-000000000102', 'agent', 'a0000000-0000-0000-0000-000000000011',
     'Process all scheduled tasks within interval ceiling',
     'zero skipped non-skippable tasks per 24h', 'perseus', 'active'),
    ('a0000000-0000-0000-0000-000000000103', 'agent', 'a0000000-0000-0000-0000-000000000012',
     'Deliver alerts within 5 seconds of event',
     'p99 alert_delivery_latency < 5000ms', 'hermes', 'active')
ON CONFLICT DO NOTHING;

-- ============================================================
-- Pattern 13: Governance / Approval System
-- ============================================================

CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(40) NOT NULL CHECK (type IN (
        'budget_override', 'autonomy_transition', 'config_change', 'capability_grant'
    )),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'approved', 'rejected', 'expired'
    )),
    requested_by VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    resolved_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_type ON approvals(type);
CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(expires_at) WHERE status = 'pending';

-- ============================================================
-- Pattern 14: Commit Metrics Tracker
-- ============================================================

CREATE TABLE IF NOT EXISTS commit_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commit_sha VARCHAR(40) UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    files_changed INTEGER NOT NULL DEFAULT 0,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_removed INTEGER NOT NULL DEFAULT 0,
    co_authored BOOLEAN NOT NULL DEFAULT false,
    agent_id VARCHAR(100),
    session_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commit_metrics_sha ON commit_metrics(commit_sha);
CREATE INDEX IF NOT EXISTS idx_commit_metrics_timestamp ON commit_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_commit_metrics_agent ON commit_metrics(agent_id);

-- ============================================================
-- Feature flags (all OFF by default)
-- ============================================================

INSERT INTO system_config (key, value) VALUES
    ('GOAL_CASCADE_ENABLED', '"false"'::jsonb),
    ('GOVERNANCE_ENABLED', '"false"'::jsonb),
    ('COMMIT_METRICS_ENABLED', '"false"'::jsonb)
ON CONFLICT (key) DO NOTHING;

COMMIT;
```

**Safety notes:**
- All `CREATE TABLE IF NOT EXISTS` — idempotent
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — idempotent, non-blocking on Postgres 11+
- `ON CONFLICT DO NOTHING` on all INSERTs — safe to re-run
- No table locks, no data rewrite — safe on live system
- Seed goals use deterministic UUIDs for idempotency
- Partial index on `approvals(expires_at)` only for pending rows — minimal overhead

---

## Tests Required

**Target: 22 new tests across 3 test files**

### `tests/test_goal_cascade.py` (new, ~8 tests)
1. `test_create_goal_returns_uuid()` — `create_goal("company", "Test goal")` returns valid UUID string
2. `test_create_goal_invalid_level_raises()` — level "invalid" raises ValueError
3. `test_resolve_goal_cascade_order()` — agent-level goal takes priority over team-level for matching agent
4. `test_resolve_goal_fallback_to_company()` — unknown task_type with no agent falls back to company goal
5. `test_update_goal_progress_aggregation()` — 2/4 children completed = 50.0% completion
6. `test_get_goal_tree_structure()` — tree has company root with team children with agent children
7. `test_complete_goal_updates_status()` — `complete_goal(id)` changes status to "completed"
8. `test_feature_flag_off_returns_none()` — all functions return None/empty when `GOAL_CASCADE_ENABLED` is off

### `tests/test_governance.py` (new, ~8 tests)
1. `test_request_approval_creates_pending()` — creates row with status "pending"
2. `test_request_approval_emits_event()` — `approval_requested` event emitted
3. `test_resolve_approval_approve()` — `resolve_approval(id, "approved", "operator")` sets status
4. `test_resolve_approval_reject()` — `resolve_approval(id, "rejected", "operator")` sets status
5. `test_resolve_already_resolved_returns_false()` — resolving a non-pending approval returns False
6. `test_check_approval_required_protected_key()` — `review_mode` requires approval
7. `test_auto_expire_stale()` — approvals past `expires_at` get status "expired"
8. `test_feature_flag_off_auto_approves()` — `get_approved()` returns True when flag is off

### `tests/test_commit_metrics.py` (new, ~6 tests)
1. `test_collect_recent_commits_parses_git_log()` — mock subprocess returns parsed commits
2. `test_detect_agent_claude()` — Co-Authored-By with "Claude" detected as agent "claude"
3. `test_detect_agent_unknown()` — Co-Authored-By with unknown name returns None
4. `test_record_commit_dedupes_on_sha()` — inserting same SHA twice returns True then no error (ON CONFLICT)
5. `test_get_metrics_summary_aggregates()` — summary includes correct totals and percentages
6. `test_feature_flag_off_skips_collection()` — `collect_and_record()` returns 0 when flag is off

### Existing test suite
- All existing tests must continue to pass (no existing code modified beyond additive changes)
- Run: `PYTHONPATH=. python3 -m pytest tests/ -v`
- Run: `ruff check shared/ tools/ hermes/ perseus/`

---

## Success Criteria

- [ ] All 3 feature flags exist and default to OFF
- [ ] Migration 028 applies cleanly on existing schema (idempotent, re-runnable)
- [ ] `goals` table populated with seed hierarchy (company → team → agent)
- [ ] `resolve_goal()` cascades correctly: task → agent → team → company
- [ ] `update_goal_progress()` aggregates child completion percentage
- [ ] `get_goal_tree()` returns nested structure for dashboard
- [ ] `task_queue.goal_tag` auto-populated when `GOAL_CASCADE_ENABLED` is on
- [ ] `approvals` table tracks pending/approved/rejected/expired states
- [ ] `review_mode` True→False transition requires approval (AEGIS fix)
- [ ] Protected config keys require approval when governance is on
- [ ] Hermes sends Telegram notification on new approval request
- [ ] `auto_expire_stale()` expires approvals past 24h
- [ ] `collect_recent_commits()` parses git log correctly
- [ ] Co-Authored-By headers detected and attributed to agent_id
- [ ] `get_metrics_summary()` returns aggregated stats for dashboard
- [ ] `GET /api/goals/tree`, `GET /api/approvals`, `GET /api/commit-metrics` return valid JSON
- [ ] All existing tests pass with all flags OFF
- [ ] 22+ new tests pass with flags ON
- [ ] `ruff check` clean on all new and modified files
- [ ] No f-string SQL anywhere in new code (parameterized queries only)
- [ ] No Conway wiring in commit metrics (standalone)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Goal cascade resolution adds latency to `insert_task()` | Medium | Low | Resolution is 4 sequential queries max; behind feature flag; goal_tag is nullable so no blocking |
| Seed goal UUIDs conflict across environments | Low | Low | Deterministic UUIDs with `ON CONFLICT DO NOTHING`; only seeds company/team/agent levels |
| Approval system blocks operator actions when Telegram is down | Medium | Medium | Approvals expire after 24h; `_enabled()` check means turning off flag bypasses entirely; dashboard provides web-based resolve |
| `git log` subprocess fails in Docker/CI | Medium | Low | `collect_recent_commits` catches subprocess errors gracefully; returns empty list; skippable=False ensures retry next day |
| INTERVAL interpolation in SQL | Low | High | `expires_hours` and `days` params are integers, not user strings; validated before query; Postgres treats `%s` as integer in INTERVAL context |
| Large git history causes slow collection | Low | Medium | `--since` limits scope to 24h; subprocess timeout of 30 seconds; only runs daily |

---

## Execution Order

1. **Migration 028** first (schema must exist before code references new tables/columns)
2. **Plan 15-01** Goal Cascade (foundational — tasks reference goal_tag)
3. **Plan 15-02** Governance / Approval System (independent module + AEGIS review_mode fix)
4. **Plan 15-03** Commit Metrics Tracker (fully standalone)
5. **Dashboard routes** after all modules exist (routes import from modules)
6. **Scheduler tasks** last (scheduler calls module functions)
7. **Test suite** — run full suite after each plan, final regression with all flags ON
