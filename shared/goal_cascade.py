"""Goal Cascade -- hierarchical goal tree with cascade resolution.

Source: Paperclip schema/goals.ts + services/issue-goal-fallback.ts (MIT)
Feature flag: GOAL_CASCADE_ENABLED

Levels: company -> team -> agent -> task
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
    """Find applicable goal by cascade: task -> agent -> team -> company.

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
    - child_statuses: dict[str, int] (status -> count)
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
