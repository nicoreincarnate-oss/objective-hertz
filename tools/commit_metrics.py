"""Commit Metrics Tracker -- collect and attribute git commit statistics.

Source: Paperclip scripts/paperclip-commit-metrics.ts (MIT)
Feature flag: COMMIT_METRICS_ENABLED

Standalone module -- NOT wired to Conway (Conway has P0 bugs).
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
    - by_agent: dict[str, int] (agent_id -> commit count)
    - daily_activity: list[dict] (date -> commit count for charting)
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
           WHERE timestamp > NOW() - make_interval(days => %s)""",
        (days,),
    )

    total = summary_row["total_commits"] if summary_row else 0
    co_authored = summary_row["co_authored_count"] if summary_row else 0

    # By agent breakdown
    agent_rows = await db.fetch_all(
        """SELECT COALESCE(agent_id, 'human') as agent, COUNT(*) as cnt
           FROM commit_metrics
           WHERE timestamp > NOW() - make_interval(days => %s)
           GROUP BY agent_id
           ORDER BY cnt DESC""",
        (days,),
    )
    by_agent = {row["agent"]: row["cnt"] for row in agent_rows}

    # Daily activity for charting
    daily_rows = await db.fetch_all(
        """SELECT DATE(timestamp) as day, COUNT(*) as cnt
           FROM commit_metrics
           WHERE timestamp > NOW() - make_interval(days => %s)
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
