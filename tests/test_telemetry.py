"""Tests for shared.telemetry — Perseus telemetry integration."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared.telemetry import MetricEvent, TelemetryCollector, collector, record, track


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector() -> TelemetryCollector:
    """Return a fresh collector (not the global singleton)."""
    return TelemetryCollector()


def _ts(minutes_ago: float = 0.0) -> float:
    return time.time() - minutes_ago * 60


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecordEvent:
    def test_record_event(self):
        tc = _make_collector()
        ev = MetricEvent(name="llm_call", agent="titan", duration_ms=120.0, cost_usd=0.003)
        tc.record(ev)
        assert len(tc._events) == 1
        assert tc._events[0] is ev
        assert tc._events[0].name == "llm_call"
        assert tc._events[0].agent == "titan"


class TestTrackContextManager:
    def test_track_context_manager_success(self):
        tc = _make_collector()

        async def _run():
            async with tc.track("llm_call", "titan", model="haiku"):
                pass  # simulate work

        asyncio.get_event_loop().run_until_complete(_run())

        assert len(tc._events) == 1
        ev = tc._events[0]
        assert ev.name == "llm_call"
        assert ev.agent == "titan"
        assert ev.success is True
        assert ev.duration_ms >= 0.0
        assert ev.metadata.get("model") == "haiku"

    def test_track_context_manager_failure(self):
        tc = _make_collector()

        async def _run():
            with pytest.raises(ValueError, match="boom"):
                async with tc.track("tool_execute", "clawdbot", tool="browser"):
                    raise ValueError("boom")

        asyncio.get_event_loop().run_until_complete(_run())

        assert len(tc._events) == 1
        ev = tc._events[0]
        assert ev.name == "tool_execute"
        assert ev.success is False


class TestMetricsSummary:
    def test_metrics_summary_filters_by_agent(self):
        tc = _make_collector()
        tc.record(MetricEvent(name="llm_call", agent="titan", duration_ms=100))
        tc.record(MetricEvent(name="llm_call", agent="hermes", duration_ms=200))
        tc.record(MetricEvent(name="tool_execute", agent="titan", duration_ms=50))

        summary = tc.get_metrics_summary(agent="titan")
        assert summary["total_events"] == 2
        # Only titan events
        for ev in tc._filter(agent="titan"):
            assert ev.agent == "titan"

    def test_metrics_summary_filters_by_time(self):
        tc = _make_collector()
        # Recent event
        tc.record(MetricEvent(name="llm_call", agent="titan", duration_ms=100))
        # Old event — 120 minutes ago
        old = MetricEvent(name="llm_call", agent="titan", duration_ms=200)
        old.timestamp = _ts(minutes_ago=120)
        tc.record(old)

        summary = tc.get_metrics_summary(last_minutes=60)
        assert summary["total_events"] == 1


class TestLLMMetrics:
    def test_llm_metrics_aggregation(self):
        tc = _make_collector()
        now = time.time()
        tc.record(MetricEvent(
            name="llm_call", agent="titan", duration_ms=100,
            cost_usd=0.01, metadata={"model": "haiku"}, timestamp=now - 30,
        ))
        tc.record(MetricEvent(
            name="llm_call", agent="titan", duration_ms=200,
            cost_usd=0.05, metadata={"model": "opus"}, timestamp=now,
        ))
        tc.record(MetricEvent(
            name="tool_execute", agent="titan", duration_ms=50,
        ))

        metrics = tc.get_llm_metrics()
        assert metrics["total_calls"] == 2
        assert metrics["avg_latency_ms"] == pytest.approx(150.0)
        assert metrics["total_cost_usd"] == pytest.approx(0.06)
        assert "haiku" in metrics["by_model"]
        assert "opus" in metrics["by_model"]
        assert metrics["by_model"]["haiku"]["count"] == 1
        assert metrics["by_model"]["opus"]["total_cost_usd"] == pytest.approx(0.05)


class TestToolMetrics:
    def test_tool_metrics_aggregation(self):
        tc = _make_collector()
        tc.record(MetricEvent(
            name="tool_execute", agent="titan", duration_ms=80,
            success=True, metadata={"tool": "browser"},
        ))
        tc.record(MetricEvent(
            name="tool_execute", agent="titan", duration_ms=120,
            success=False, metadata={"tool": "browser"},
        ))
        tc.record(MetricEvent(
            name="tool_execute", agent="clawdbot", duration_ms=50,
            success=True, metadata={"tool": "email_send"},
        ))

        metrics = tc.get_tool_metrics()
        assert metrics["total_executions"] == 3
        assert metrics["success_rate"] == pytest.approx(2 / 3)
        assert metrics["by_tool"]["browser"]["count"] == 2
        assert metrics["by_tool"]["browser"]["success"] == 1
        assert metrics["by_tool"]["email_send"]["count"] == 1


class TestOJUnavailable:
    def test_oj_unavailable_graceful(self):
        """Collector works even when OJ telemetry module is not importable."""
        tc = _make_collector()
        # _oj_store is None by default (no initialize called)
        assert tc._oj_store is None

        # Recording should succeed silently
        tc.record(MetricEvent(name="llm_call", agent="titan", duration_ms=100))
        assert len(tc._events) == 1

        # Querying should work
        summary = tc.get_metrics_summary()
        assert summary["total_events"] == 1


class TestFlush:
    def test_flush_clears_buffer(self):
        tc = _make_collector()
        tc.record(MetricEvent(name="llm_call", agent="titan", duration_ms=100))
        tc.record(MetricEvent(name="tool_execute", agent="hermes", duration_ms=50))
        assert len(tc._events) == 2

        asyncio.get_event_loop().run_until_complete(tc.flush())
        assert len(tc._events) == 0
