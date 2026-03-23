"""Shared pipeline state assessment — used by both Perseus scheduler and Titan A2A.

Extracted from perseus/daemon.py so any daemon can serve pipeline state
without importing the Perseus daemon.
"""

from __future__ import annotations

from shared import db


async def assess_pipeline_state() -> dict:
    """Build a snapshot of the full pipeline for strategic decision-making."""
    try:
        rows = await db.fetch_all(
            "SELECT status, COUNT(*) as count FROM clients GROUP BY status"
        )
        stage_counts = {r["status"]: r["count"] for r in rows}
    except Exception:
        stage_counts = {}

    try:
        pending_tasks = await db.fetch_all(
            """SELECT task_type, COUNT(*) as count FROM task_queue
               WHERE status IN ('pending', 'running')
               GROUP BY task_type"""
        )
        task_backlog = {r["task_type"]: r["count"] for r in pending_tasks}
    except Exception:
        task_backlog = {}

    try:
        recent_errors = await db.fetch_val(
            """SELECT COUNT(*) FROM events
               WHERE event_type = 'pipeline_error'
               AND created_at > NOW() - INTERVAL '1 hour'"""
        ) or 0
    except Exception:
        recent_errors = 0

    try:
        pending_reviews = await db.fetch_val(
            "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
        ) or 0
    except Exception:
        pending_reviews = 0

    return {
        "stage_counts": stage_counts,
        "task_backlog": task_backlog,
        "recent_errors": recent_errors,
        "pending_reviews": pending_reviews,
        "hot_leads": stage_counts.get("interested", 0) + stage_counts.get("demo_built", 0),
        "ready_to_close": stage_counts.get("proposal_sent", 0) + stage_counts.get("negotiating", 0),
        "ready_to_deliver": stage_counts.get("closed", 0),
        "ready_to_invoice": stage_counts.get("deployed", 0),
        "top_of_funnel": stage_counts.get("discovered", 0) + stage_counts.get("researched", 0),
        "outreach_active": stage_counts.get("email_drafted", 0) + stage_counts.get("email_sent", 0),
    }
