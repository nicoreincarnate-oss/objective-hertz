"""Tests for tools/commit_metrics.py — Commit Metrics Tracker."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    """Enable the COMMIT_METRICS_ENABLED flag for all tests by default."""
    monkeypatch.setenv("COMMIT_METRICS_ENABLED", "true")


@pytest.fixture
def mock_db():
    """Mock shared.db module functions."""
    with patch("tools.commit_metrics.db") as db:
        db.execute = AsyncMock()
        db.fetch_one = AsyncMock(return_value=None)
        db.fetch_all = AsyncMock(return_value=[])
        yield db


# ── Tests ───────────────────────────────────────────────────────────

def test_collect_recent_commits_parses_git_log():
    """Mock subprocess returns parsed commits."""
    from tools.commit_metrics import collect_recent_commits

    mock_output = (
        "abc123def456789012345678901234567890|2026-03-30T10:00:00+00:00|feat: add feature\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>\n"
        "10\t5\tsrc/main.py\n"
        "3\t1\ttests/test_main.py\n"
        "---END---\n"
        "def456abc789012345678901234567890abcd|2026-03-30T11:00:00+00:00|fix: bug fix\n"
        "2\t1\tsrc/fix.py\n"
        "---END---"
    )

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = mock_output

    with patch("tools.commit_metrics.subprocess.run", return_value=mock_result):
        commits = collect_recent_commits(since_hours=24)

    assert len(commits) == 2
    # First commit has co-author
    assert commits[0]["co_authored"] is True
    assert commits[0]["agent_id"] == "claude"
    assert commits[0]["files_changed"] == 2
    assert commits[0]["lines_added"] == 13
    assert commits[0]["lines_removed"] == 6
    # Second commit is human-only
    assert commits[1]["co_authored"] is False
    assert commits[1]["agent_id"] is None


def test_detect_agent_claude():
    """Co-Authored-By with 'Claude' detected as agent 'claude'."""
    from tools.commit_metrics import _detect_agent

    assert _detect_agent("Claude Sonnet", "noreply@anthropic.com") == "claude"
    assert _detect_agent("Some Bot", "claude@example.com") == "claude"
    assert _detect_agent("Bot", "noreply@anthropic.com") == "claude"


def test_detect_agent_unknown():
    """Co-Authored-By with unknown name returns None."""
    from tools.commit_metrics import _detect_agent

    assert _detect_agent("Random Dev", "dev@example.com") is None


@pytest.mark.asyncio
async def test_record_commit_dedupes_on_sha(mock_db):
    """Inserting same SHA twice uses ON CONFLICT DO NOTHING."""
    from tools.commit_metrics import record_commit_metrics
    from datetime import datetime, timezone

    commit = {
        "sha": "abc123",
        "timestamp": datetime.now(timezone.utc),
        "files_changed": 1,
        "lines_added": 10,
        "lines_removed": 5,
        "co_authored": False,
        "agent_id": None,
    }

    result = await record_commit_metrics(commit)
    assert result is True
    call_args = mock_db.execute.call_args[0]
    assert "ON CONFLICT (commit_sha) DO NOTHING" in call_args[0]


@pytest.mark.asyncio
async def test_get_metrics_summary_aggregates(mock_db):
    """Summary includes correct totals and percentages."""
    from tools.commit_metrics import get_metrics_summary

    mock_db.fetch_one = AsyncMock(return_value={
        "total_commits": 10,
        "total_files_changed": 50,
        "total_lines_added": 500,
        "total_lines_removed": 200,
        "co_authored_count": 7,
    })
    mock_db.fetch_all = AsyncMock(side_effect=[
        # by_agent
        [{"agent": "claude", "cnt": 7}, {"agent": "human", "cnt": 3}],
        # daily_activity
        [{"day": "2026-03-30", "cnt": 5}, {"day": "2026-03-29", "cnt": 5}],
    ])

    summary = await get_metrics_summary(days=30)
    assert summary["enabled"] is True
    assert summary["total_commits"] == 10
    assert summary["co_authored_count"] == 7
    assert summary["co_authored_pct"] == 70.0
    assert summary["by_agent"]["claude"] == 7
    assert len(summary["daily_activity"]) == 2


@pytest.mark.asyncio
async def test_feature_flag_off_skips_collection(monkeypatch, mock_db):
    """collect_and_record returns 0 when flag is off."""
    monkeypatch.setenv("COMMIT_METRICS_ENABLED", "false")
    from tools.commit_metrics import collect_and_record

    result = await collect_and_record()
    assert result == 0
    mock_db.execute.assert_not_called()
