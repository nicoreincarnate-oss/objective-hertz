"""
Bottleneck Detector — Scan pipeline metrics for code-level bottlenecks.

Analyzes: pipeline errors, stage durations, failure rates, resource usage.
Outputs scored bottlenecks for Ruflo to fix.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("perseus.ruflo.bottleneck")


async def detect_bottlenecks(days: int = 7) -> list[dict]:
    """Scan recent pipeline errors and performance metrics for bottlenecks.

    Returns list of bottlenecks sorted by ROI score (highest first):
    [{"description": str, "file": str, "error": str, "score": float, "category": str}]
    """
    bottlenecks = []

    try:
        from shared.db import fetch_all

        # 1. Pipeline errors by stage (most errors = biggest bottleneck)
        errors = await fetch_all(
            """SELECT payload->>'stage' as stage,
                      payload->>'error' as error,
                      COUNT(*) as count
               FROM events
               WHERE event_type = 'pipeline_error'
               AND created_at > NOW() - INTERVAL '%s days'
               GROUP BY stage, error
               ORDER BY count DESC LIMIT 10""",
            (days,),
        )
        for err in errors:
            stage = err.get("stage", "unknown")
            bottlenecks.append({
                "description": f"Pipeline error in {stage}: {err.get('error', '')[:100]}",
                "file": f"titan/pipeline/{stage}.py",
                "error": err.get("error", ""),
                "score": score_bottleneck({"count": err.get("count", 0), "category": "pipeline_error"}),
                "category": "pipeline_error",
            })

        # 2. Slow stages (task execution time > 60s)
        slow = await fetch_all(
            """SELECT task_type, AVG(EXTRACT(EPOCH FROM (completed_at - claimed_at))) as avg_seconds,
                      COUNT(*) as count
               FROM task_queue
               WHERE completed_at IS NOT NULL
               AND claimed_at IS NOT NULL
               AND created_at > NOW() - INTERVAL '%s days'
               GROUP BY task_type
               HAVING AVG(EXTRACT(EPOCH FROM (completed_at - claimed_at))) > 60
               ORDER BY avg_seconds DESC LIMIT 5""",
            (days,),
        )
        for s in slow:
            bottlenecks.append({
                "description": f"Slow task: {s.get('task_type', '')} avg {s.get('avg_seconds', 0):.0f}s",
                "file": "",
                "error": "",
                "score": score_bottleneck({"avg_seconds": s.get("avg_seconds", 0), "category": "slow_task"}),
                "category": "slow_task",
            })

        # 3. High failure rate tasks
        failures = await fetch_all(
            """SELECT task_type,
                      COUNT(*) FILTER (WHERE status = 'failed') as failed,
                      COUNT(*) as total
               FROM task_queue
               WHERE created_at > NOW() - INTERVAL '%s days'
               GROUP BY task_type
               HAVING COUNT(*) FILTER (WHERE status = 'failed') > 2
               ORDER BY failed DESC LIMIT 5""",
            (days,),
        )
        for f in failures:
            total = f.get("total", 1)
            failed = f.get("failed", 0)
            rate = failed / total if total > 0 else 0
            if rate > 0.1:
                bottlenecks.append({
                    "description": f"High failure rate: {f.get('task_type', '')} ({rate:.0%})",
                    "file": "",
                    "error": "",
                    "score": score_bottleneck({"failure_rate": rate, "category": "failure_rate"}),
                    "category": "failure_rate",
                })

    except Exception as e:
        logger.debug(f"Bottleneck detection failed: {e}")

    # Sort by score descending
    bottlenecks.sort(key=lambda x: x.get("score", 0), reverse=True)
    return bottlenecks


def score_bottleneck(bottleneck: dict) -> float:
    """ROI scoring: estimated fix impact vs estimated effort.

    Returns 0.0-1.0, higher = more impactful to fix.
    """
    category = bottleneck.get("category", "")

    if category == "pipeline_error":
        count = bottleneck.get("count", 0)
        return min(1.0, count / 20)  # 20+ errors → max score

    if category == "slow_task":
        avg_s = bottleneck.get("avg_seconds", 0)
        return min(1.0, avg_s / 300)  # 5+ minutes → max score

    if category == "failure_rate":
        rate = bottleneck.get("failure_rate", 0)
        return min(1.0, rate * 2)  # 50%+ failure → max score

    return 0.5
