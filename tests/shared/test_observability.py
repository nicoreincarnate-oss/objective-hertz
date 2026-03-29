"""Tests for shared.observability LLM metrics (Phase 0b)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from shared.observability import get_metrics_summary, record_llm_call


class TestRecordLLMCall:
    """test_record_llm_call: mock DB execute, verify correct INSERT params."""

    @pytest.mark.asyncio
    async def test_inserts_correct_params(self):
        mock_execute = AsyncMock()
        with patch("shared.observability.execute", mock_execute, create=True):
            # Patch the import inside record_llm_call
            with patch.dict("sys.modules", {}):
                pass
            with patch("shared.db.execute", mock_execute):
                await record_llm_call(
                    daemon="titan",
                    model="sonnet",
                    call_type="generate",
                    input_tokens=100,
                    output_tokens=50,
                    latency_ms=1200,
                    cost_usd=0.001050,
                    success=True,
                    error_type=None,
                )

        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "INSERT INTO llm_metrics" in sql
        assert params[0] == "titan"
        assert params[1] == "sonnet"
        assert params[2] == "generate"
        assert params[3] == 100  # input_tokens
        assert params[4] == 50  # output_tokens
        assert params[5] == 1200  # latency_ms
        assert params[7] is True  # success
        assert params[8] is None  # error_type

    @pytest.mark.asyncio
    async def test_records_error_type(self):
        mock_execute = AsyncMock()
        with patch("shared.db.execute", mock_execute):
            await record_llm_call(
                daemon="hermes",
                model="haiku",
                call_type="classify",
                input_tokens=50,
                output_tokens=10,
                latency_ms=300,
                cost_usd=0.0001,
                success=False,
                error_type="TimeoutError",
            )

        params = mock_execute.call_args[0][1]
        assert params[7] is False
        assert params[8] == "TimeoutError"


class TestGetMetricsSummary:
    """test_get_metrics_summary: mock DB fetch, verify aggregation returned."""

    @pytest.mark.asyncio
    async def test_returns_daemon_aggregation(self):
        mock_rows = [
            {
                "daemon": "titan",
                "total_calls": 42,
                "avg_latency_ms": 800,
                "error_rate_pct": 2.38,
                "total_cost_usd": 0.05,
                "total_input_tokens": 5000,
                "total_output_tokens": 3000,
            }
        ]
        mock_fetch = AsyncMock(return_value=mock_rows)
        with patch("shared.db.fetch_all", mock_fetch):
            result = await get_metrics_summary(hours=12)

        assert result["hours"] == 12
        assert len(result["daemons"]) == 1
        assert result["daemons"][0]["daemon"] == "titan"
        assert result["daemons"][0]["total_calls"] == 42

    @pytest.mark.asyncio
    async def test_filters_by_daemon(self):
        mock_fetch = AsyncMock(return_value=[])
        with patch("shared.db.fetch_all", mock_fetch):
            result = await get_metrics_summary(daemon="hermes", hours=6)

        assert result["hours"] == 6
        sql = mock_fetch.call_args[0][0]
        assert "daemon = %s" in sql
        params = mock_fetch.call_args[0][1]
        assert "hermes" in params


class TestRecordHandlesDBError:
    """test_record_handles_db_error: DB raises, function logs warning and doesn't raise."""

    @pytest.mark.asyncio
    async def test_db_error_does_not_raise(self, caplog):
        mock_execute = AsyncMock(side_effect=ConnectionError("DB offline"))
        with patch("shared.db.execute", mock_execute):
            with caplog.at_level(logging.WARNING):
                # This should NOT raise
                await record_llm_call(
                    daemon="titan",
                    model="sonnet",
                    call_type="generate",
                    input_tokens=100,
                    output_tokens=50,
                    latency_ms=500,
                    cost_usd=0.001,
                    success=True,
                )

        assert "Failed to record LLM metrics" in caplog.text

    @pytest.mark.asyncio
    async def test_summary_db_error_returns_empty(self, caplog):
        mock_fetch = AsyncMock(side_effect=ConnectionError("DB offline"))
        with patch("shared.db.fetch_all", mock_fetch):
            with caplog.at_level(logging.WARNING):
                result = await get_metrics_summary()

        assert result["daemons"] == []
        assert "error" in result
