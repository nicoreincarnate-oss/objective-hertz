"""Perseus telemetry integration via OJ telemetry module.

Provides unified metrics collection across all agents:
- LLM call latency, cost, token usage
- Tool execution time and success rate
- Pipeline stage throughput
- Agent health metrics

OJ telemetry uses SQLite-backed storage with TelemetryStore / TelemetryAggregator.
This module bridges Perseus agents into that infrastructure while remaining
fully functional even when OJ is unavailable (graceful degradation).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- OJ telemetry import with graceful fallback ---
_oj_store_cls: type | None = None
_oj_record_cls: type | None = None

try:
    from openjarvis.telemetry.store import TelemetryStore as _OJStore
    from openjarvis.core.types import TelemetryRecord as _OJRecord

    _oj_store_cls = _OJStore
    _oj_record_cls = _OJRecord
except ImportError:
    logger.debug("OJ telemetry not available; running in standalone mode")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MetricEvent:
    """A single metric data point."""

    name: str  # "llm_call", "tool_execute", "pipeline_stage"
    agent: str  # "titan", "clawdbot", "hermes"
    duration_ms: float = 0.0
    cost_usd: float = 0.0
    success: bool = True
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

_MAX_BUFFER = 10_000  # in-memory cap before oldest events are dropped


class TelemetryCollector:
    """Collects and stores metrics from all agents.

    Holds an in-memory ring of ``MetricEvent`` objects and optionally
    forwards LLM-related events to the OJ ``TelemetryStore`` for
    persistent SQLite storage.
    """

    def __init__(self) -> None:
        self._events: list[MetricEvent] = []
        self._oj_store: Any | None = None
        self._initialized: bool = False

    async def initialize(self, db_path: str | None = None) -> None:
        """Start telemetry collection.  Wire to OJ telemetry if available.

        Parameters
        ----------
        db_path:
            Optional path for the OJ SQLite database.  When *None* the OJ
            store is not created (metrics stay in-memory only).
        """
        if self._initialized:
            return

        if db_path and _oj_store_cls is not None:
            try:
                self._oj_store = _oj_store_cls(db_path)
                logger.info("OJ telemetry store opened at %s", db_path)
            except Exception as exc:
                logger.warning("Failed to open OJ telemetry store: %s", exc)
                self._oj_store = None

        self._initialized = True

    # -- recording ----------------------------------------------------------

    def record(self, event: MetricEvent) -> None:
        """Record a metric event into the in-memory buffer.

        If the buffer exceeds ``_MAX_BUFFER`` the oldest events are
        dropped.  LLM call events are also forwarded to the OJ store
        when available.
        """
        self._events.append(event)
        if len(self._events) > _MAX_BUFFER:
            self._events = self._events[-_MAX_BUFFER:]

        # Forward LLM events to OJ persistent store
        if self._oj_store is not None and _oj_record_cls is not None and event.name == "llm_call":
            try:
                rec = _oj_record_cls(
                    timestamp=event.timestamp,
                    model_id=event.metadata.get("model", "unknown"),
                    engine=event.metadata.get("engine", ""),
                    agent=event.agent,
                    prompt_tokens=event.metadata.get("prompt_tokens", 0),
                    completion_tokens=event.metadata.get("completion_tokens", 0),
                    total_tokens=event.metadata.get("total_tokens", 0),
                    latency_seconds=event.duration_ms / 1000.0,
                    cost_usd=event.cost_usd,
                )
                self._oj_store.record(rec)
            except Exception as exc:
                logger.debug("OJ store forward failed: %s", exc)

    @asynccontextmanager
    async def track(self, name: str, agent: str, **metadata: Any):
        """Context manager for tracking operation duration and success.

        Usage::

            async with telemetry.track("llm_call", "titan", model="haiku"):
                result = await llm.generate(...)
        """
        start = time.time()
        success = True
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            elapsed_ms = (time.time() - start) * 1000.0
            self.record(
                MetricEvent(
                    name=name,
                    agent=agent,
                    duration_ms=elapsed_ms,
                    success=success,
                    metadata=metadata,
                    timestamp=start,
                )
            )

    # -- query helpers ------------------------------------------------------

    def _filter(
        self,
        *,
        agent: str | None = None,
        name: str | None = None,
        last_minutes: int | None = None,
    ) -> list[MetricEvent]:
        cutoff = time.time() - (last_minutes * 60) if last_minutes else 0.0
        out: list[MetricEvent] = []
        for ev in self._events:
            if agent and ev.agent != agent:
                continue
            if name and ev.name != name:
                continue
            if last_minutes and ev.timestamp < cutoff:
                continue
            out.append(ev)
        return out

    def get_metrics_summary(
        self,
        agent: str | None = None,
        last_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get aggregated metrics for dashboard display."""
        events = self._filter(agent=agent, last_minutes=last_minutes)
        if not events:
            return {
                "total_events": 0,
                "success_rate": 0.0,
                "avg_duration_ms": 0.0,
                "total_cost_usd": 0.0,
                "by_name": {},
            }

        total = len(events)
        successes = sum(1 for e in events if e.success)
        avg_dur = sum(e.duration_ms for e in events) / total
        total_cost = sum(e.cost_usd for e in events)

        by_name: dict[str, dict] = {}
        for ev in events:
            bucket = by_name.setdefault(ev.name, {"count": 0, "success": 0, "total_ms": 0.0})
            bucket["count"] += 1
            if ev.success:
                bucket["success"] += 1
            bucket["total_ms"] += ev.duration_ms

        return {
            "total_events": total,
            "success_rate": successes / total if total else 0.0,
            "avg_duration_ms": avg_dur,
            "total_cost_usd": total_cost,
            "by_name": by_name,
        }

    def get_llm_metrics(self) -> dict[str, Any]:
        """LLM-specific: calls/min, avg latency, cost breakdown by model."""
        events = self._filter(name="llm_call")
        if not events:
            return {
                "total_calls": 0,
                "avg_latency_ms": 0.0,
                "total_cost_usd": 0.0,
                "by_model": {},
            }

        total = len(events)
        avg_lat = sum(e.duration_ms for e in events) / total
        total_cost = sum(e.cost_usd for e in events)

        # Time span for calls/min
        timestamps = [e.timestamp for e in events]
        span_min = (max(timestamps) - min(timestamps)) / 60.0 if len(timestamps) > 1 else 1.0
        calls_per_min = total / span_min if span_min > 0 else float(total)

        by_model: dict[str, dict] = {}
        for ev in events:
            model = ev.metadata.get("model", "unknown")
            bucket = by_model.setdefault(model, {"count": 0, "total_cost_usd": 0.0, "total_ms": 0.0})
            bucket["count"] += 1
            bucket["total_cost_usd"] += ev.cost_usd
            bucket["total_ms"] += ev.duration_ms

        return {
            "total_calls": total,
            "calls_per_min": calls_per_min,
            "avg_latency_ms": avg_lat,
            "total_cost_usd": total_cost,
            "by_model": by_model,
        }

    def get_tool_metrics(self) -> dict[str, Any]:
        """Tool-specific: execution count, success rate, avg duration by tool."""
        events = self._filter(name="tool_execute")
        if not events:
            return {"total_executions": 0, "success_rate": 0.0, "by_tool": {}}

        total = len(events)
        successes = sum(1 for e in events if e.success)

        by_tool: dict[str, dict] = {}
        for ev in events:
            tool = ev.metadata.get("tool", "unknown")
            bucket = by_tool.setdefault(tool, {"count": 0, "success": 0, "total_ms": 0.0})
            bucket["count"] += 1
            if ev.success:
                bucket["success"] += 1
            bucket["total_ms"] += ev.duration_ms

        return {
            "total_executions": total,
            "success_rate": successes / total if total else 0.0,
            "by_tool": by_tool,
        }

    def get_pipeline_metrics(self) -> dict[str, Any]:
        """Pipeline-specific: throughput per stage, bottleneck identification."""
        events = self._filter(name="pipeline_stage")
        if not events:
            return {"total_stages": 0, "by_stage": {}, "bottleneck": None}

        by_stage: dict[str, dict] = {}
        for ev in events:
            stage = ev.metadata.get("stage", "unknown")
            bucket = by_stage.setdefault(stage, {"count": 0, "total_ms": 0.0, "success": 0})
            bucket["count"] += 1
            bucket["total_ms"] += ev.duration_ms
            if ev.success:
                bucket["success"] += 1

        # Compute avg and find bottleneck
        bottleneck: str | None = None
        max_avg = 0.0
        for stage, data in by_stage.items():
            data["avg_ms"] = data["total_ms"] / data["count"] if data["count"] else 0.0
            if data["avg_ms"] > max_avg:
                max_avg = data["avg_ms"]
                bottleneck = stage

        return {
            "total_stages": len(events),
            "by_stage": by_stage,
            "bottleneck": bottleneck,
        }

    async def flush(self) -> None:
        """Write buffered metrics to persistent storage and clear buffer."""
        if self._oj_store is not None:
            # All LLM events were already forwarded on record(); for non-LLM
            # events we could extend OJ schema later.  For now flush = clear.
            pass
        self._events.clear()


# ---------------------------------------------------------------------------
# Global singleton + convenience
# ---------------------------------------------------------------------------

collector = TelemetryCollector()


def record(event: MetricEvent) -> None:
    """Record a metric event on the global collector."""
    collector.record(event)


def track(name: str, agent: str, **metadata: Any):
    """Return an async context manager that tracks an operation on the global collector."""
    return collector.track(name, agent, **metadata)
