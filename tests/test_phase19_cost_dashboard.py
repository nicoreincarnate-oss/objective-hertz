"""Tests for shared/cost_dashboard.py — Phase 19 per-agent cost tracking."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fake shared.db module — psycopg is not available in the test environment
# ---------------------------------------------------------------------------

_fake_db = types.ModuleType("shared.db")
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_one = AsyncMock(return_value=None)
_fake_db.execute = AsyncMock()
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.init_pool = AsyncMock()
_fake_db.close_pool = AsyncMock()
_fake_db.fetch_val = AsyncMock(return_value=None)
sys.modules.setdefault("shared.db", _fake_db)

# Must also stub transitive deps that shared.db normally provides
for _mod_name in (
    "psycopg", "psycopg.rows", "psycopg_pool",
    "shared.observability",
):
    if _mod_name not in sys.modules:
        _stub = types.ModuleType(_mod_name)
        if _mod_name == "shared.observability":
            _stub.capture_exception = lambda *a, **kw: None
            _stub.observe_db_query = lambda *a, **kw: None
            _stub.enrich_payload_with_context = lambda p: p or {}
        sys.modules[_mod_name] = _stub

from shared.cost_dashboard import (  # noqa: E402
    _extract_model_tier,
    format_cost_report,
    get_agent_cost_summary,
    get_weekly_agent_cost_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously for tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_rows_grouped() -> list[dict]:
    """Simulated fetch_all result for the grouped weekly report query."""
    return [
        {
            "agent_name": "email_draft",
            "call_count": 10,
            "total_cost": 1.2500,
            "descriptions": "email_draft: claude-sonnet ~800tok",
        },
        {
            "agent_name": "classify",
            "call_count": 50,
            "total_cost": 0.3000,
            "descriptions": "classify: claude-haiku ~50tok",
        },
        {
            "agent_name": "untagged",
            "call_count": 5,
            "total_cost": 0.1000,
            "descriptions": "claude-sonnet ~400tok",
        },
    ]


def _make_rows_flat() -> list[dict]:
    """Simulated fetch_all result for the flat summary query."""
    return [
        {"agent_name": "email_draft", "amount": 0.50, "description": "email_draft: claude-sonnet ~800tok"},
        {"agent_name": "email_draft", "amount": 0.75, "description": "email_draft: claude-sonnet ~1200tok"},
        {"agent_name": "classify", "amount": 0.10, "description": "classify: claude-haiku ~50tok"},
        {"agent_name": "classify", "amount": 0.20, "description": "classify: claude-haiku ~80tok"},
        {"agent_name": "untagged", "amount": 0.10, "description": "claude-opus ~200tok"},
    ]


# ---------------------------------------------------------------------------
# _extract_model_tier
# ---------------------------------------------------------------------------

class TestExtractModelTier:
    def test_haiku(self):
        assert _extract_model_tier("classify: claude-haiku ~50tok") == "haiku"

    def test_sonnet(self):
        assert _extract_model_tier("email_draft: claude-sonnet ~800tok") == "sonnet"

    def test_opus(self):
        assert _extract_model_tier("claude-opus ~200tok") == "opus"

    def test_none_description(self):
        assert _extract_model_tier(None) == "unknown"

    def test_empty_description(self):
        assert _extract_model_tier("") == "unknown"

    def test_no_tier_found(self):
        assert _extract_model_tier("some random text") == "unknown"


# ---------------------------------------------------------------------------
# get_weekly_agent_cost_report
# ---------------------------------------------------------------------------

class TestWeeklyReport:
    def test_flag_off_returns_none(self):
        """With flag off, functions return None."""
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": ""}, clear=False):
            result = _run(get_weekly_agent_cost_report())
            assert result is None

    def test_weekly_report_groups_by_agent(self):
        """Mock DB rows, verify grouping and sort order."""
        mock_rows = _make_rows_grouped()
        _fake_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            report = _run(get_weekly_agent_cost_report(days=7))

        assert report is not None
        assert len(report) == 3

        # Sorted by cost descending (query ORDER BY total_cost DESC)
        assert report[0]["agent_name"] == "email_draft"
        assert report[0]["total_cost_usd"] == 1.25
        assert report[0]["call_count"] == 10
        assert report[0]["avg_cost_per_call"] == pytest.approx(0.125, abs=0.001)

        assert report[1]["agent_name"] == "classify"
        assert report[1]["total_cost_usd"] == 0.30

        assert report[2]["agent_name"] == "untagged"

    def test_empty_report_no_crash(self):
        """No data returns empty list gracefully."""
        _fake_db.fetch_all = AsyncMock(return_value=[])

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            report = _run(get_weekly_agent_cost_report(days=7))

        assert report is not None
        assert report == []

    def test_db_error_returns_empty(self):
        """Database errors return empty list, not crash."""
        _fake_db.fetch_all = AsyncMock(side_effect=ConnectionError("conn refused"))

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            report = _run(get_weekly_agent_cost_report(days=7))

        assert report is not None
        assert report == []


# ---------------------------------------------------------------------------
# get_agent_cost_summary
# ---------------------------------------------------------------------------

class TestCostSummary:
    def test_flag_off_returns_none(self):
        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": ""}, clear=False):
            result = _run(get_agent_cost_summary())
            assert result is None

    def test_summary_calculates_projected_monthly(self):
        """7-day cost * (30.44/7) ~ monthly."""
        mock_rows = _make_rows_flat()
        _fake_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            summary = _run(get_agent_cost_summary(days=7))

        assert summary is not None
        total_7d = 0.50 + 0.75 + 0.10 + 0.20 + 0.10  # 1.65
        assert summary["total_cost_7d"] == pytest.approx(total_7d, abs=0.01)

        # projected = total * (30.44 / 7)
        expected_monthly = total_7d * (30.44 / 7)
        assert summary["projected_monthly"] == pytest.approx(expected_monthly, abs=0.1)

    def test_summary_top_3_agents(self):
        mock_rows = _make_rows_flat()
        _fake_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            summary = _run(get_agent_cost_summary(days=7))

        assert summary is not None
        top_3 = summary["top_3_agents"]
        assert len(top_3) == 3
        # email_draft is highest (0.50 + 0.75 = 1.25)
        assert top_3[0]["agent"] == "email_draft"
        assert top_3[0]["cost_usd"] == pytest.approx(1.25, abs=0.01)

    def test_summary_cost_by_model_tier(self):
        mock_rows = _make_rows_flat()
        _fake_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            summary = _run(get_agent_cost_summary(days=7))

        assert summary is not None
        tiers = summary["cost_by_model_tier"]
        assert "sonnet" in tiers
        assert "haiku" in tiers
        assert "opus" in tiers
        assert tiers["sonnet"] == pytest.approx(1.25, abs=0.01)
        assert tiers["haiku"] == pytest.approx(0.30, abs=0.01)
        assert tiers["opus"] == pytest.approx(0.10, abs=0.01)

    def test_summary_db_error_returns_empty(self):
        _fake_db.fetch_all = AsyncMock(side_effect=ConnectionError("db down"))

        with patch.dict(os.environ, {"ANATOMY_COST_DASHBOARD": "true"}, clear=False):
            summary = _run(get_agent_cost_summary(days=7))

        assert summary is not None
        assert summary["total_cost_7d"] == 0.0
        assert summary["projected_monthly"] == 0.0


# ---------------------------------------------------------------------------
# format_cost_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_flag_off_returns_none(self):
        """None input (flag off) returns None."""
        assert format_cost_report(None) is None

    def test_empty_report(self):
        result = format_cost_report([])
        assert result is not None
        assert "No cost data" in result

    def test_format_report_readable(self):
        """Formatted output contains agent names and costs."""
        report = [
            {
                "agent_name": "email_draft",
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 1.2500,
                "call_count": 10,
                "avg_cost_per_call": 0.125000,
            },
            {
                "agent_name": "classify",
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 0.3000,
                "call_count": 50,
                "avg_cost_per_call": 0.006000,
            },
        ]
        formatted = format_cost_report(report)
        assert formatted is not None
        assert "email_draft" in formatted
        assert "classify" in formatted
        assert "1.2500" in formatted
        assert "0.3000" in formatted
        assert "TOTAL" in formatted
        assert "1.5500" in formatted  # 1.25 + 0.30
        # Has a header line
        assert "Agent" in formatted
        assert "Calls" in formatted
        assert "Cost ($)" in formatted
