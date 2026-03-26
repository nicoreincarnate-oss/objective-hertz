"""
Shared observability primitives for Objective Hertz.

Provides:
- structured trace and correlation context
- optional Sentry error aggregation
- optional Prometheus metrics and exporters
- asyncio exception capture helpers
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
import uuid
from typing import Any

from shared.config import config

logger = logging.getLogger("perseus.observability")

try:
    import sentry_sdk
except Exception:  # pragma: no cover - optional dependency
    sentry_sdk = None  # type: ignore[assignment]

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        start_http_server,
    )
except Exception:  # pragma: no cover - optional dependency
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"  # type: ignore[misc]

    class _NoOpMetric:  # type: ignore[no-redef]
        """Stub metric that silently discards all operations."""
        def labels(self, **kw): return self  # type: ignore[empty-body]
        def inc(self, *a, **kw): pass
        def dec(self, *a, **kw): pass
        def set(self, *a, **kw): pass
        def observe(self, *a, **kw): pass
        def __init__(self, *a, **kw): pass

    Counter = Gauge = Histogram = _NoOpMetric  # type: ignore[misc,assignment]
    def generate_latest(*a, **kw) -> bytes: return b""  # type: ignore[no-redef]  # noqa: E704
    def start_http_server(*a, **kw) -> None: return None  # type: ignore[no-redef]  # noqa: E704


_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="",
)
_task_id: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")

_DEFAULT_METRICS_PORTS = {
    "orchestrator": 9100,
    "titan": 9101,
    "hermes": 9102,
    "clawdbot": 9103,
}
_INITIALIZED_SERVICES: set[str] = set()
_METRICS_SERVERS: set[str] = set()
_SENTRY_INITIALIZED = False


def _enabled_in_tests() -> bool:
    return "PYTEST_CURRENT_TEST" not in os.environ


def _metric_counter(name: str, documentation: str, labels: tuple[str, ...]):
    return Counter(name, documentation, labels)


def _metric_gauge(name: str, documentation: str, labels: tuple[str, ...]):
    return Gauge(name, documentation, labels)


def _metric_histogram(name: str, documentation: str, labels: tuple[str, ...]):
    return Histogram(
        name,
        documentation,
        labels,
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )


AGENT_STARTS = _metric_counter(
    "objective_hertz_agent_starts_total",
    "Number of Objective Hertz agents started",
    ("agent",),
)
AGENT_SHUTDOWNS = _metric_counter(
    "objective_hertz_agent_shutdowns_total",
    "Number of Objective Hertz agents shutdown",
    ("agent",),
)
EVENTS_EMITTED = _metric_counter(
    "objective_hertz_events_emitted_total",
    "Events emitted by Objective Hertz agents",
    ("agent", "event_type"),
)
TASK_CLAIMS = _metric_counter(
    "objective_hertz_task_claims_total",
    "Tasks claimed by Objective Hertz agents",
    ("agent",),
)
TASK_COMPLETIONS = _metric_counter(
    "objective_hertz_task_completions_total",
    "Tasks completed by Objective Hertz agents",
    ("agent",),
)
TASK_FAILURES = _metric_counter(
    "objective_hertz_task_failures_total",
    "Task failures reported by Objective Hertz agents",
    ("agent",),
)
ACTIVE_WORK = _metric_gauge(
    "objective_hertz_active_work",
    "Active in-flight work items by agent",
    ("agent",),
)
WORK_DURATION = _metric_histogram(
    "objective_hertz_work_duration_seconds",
    "Time spent on tracked work items",
    ("agent", "work_kind"),
)
EXCEPTIONS = _metric_counter(
    "objective_hertz_exceptions_total",
    "Exceptions captured by Objective Hertz",
    ("service", "category"),
)
DB_QUERY_DURATION = _metric_histogram(
    "objective_hertz_db_query_duration_seconds",
    "Database query latency by operation",
    ("operation", "success"),
)


def set_observability_context(
    *,
    trace_id: str = "",
    correlation_id: str = "",
    task_id: str = "",
) -> None:
    """Bind trace context for logs and error reports."""
    if trace_id:
        _trace_id.set(trace_id)
    if correlation_id:
        _correlation_id.set(correlation_id)
    if task_id:
        _task_id.set(task_id)


def ensure_trace_context(
    *,
    trace_id: str = "",
    correlation_id: str = "",
    task_id: str = "",
    request_id: str = "",
) -> dict[str, str]:
    """Ensure a complete trace context exists and return it."""
    current_trace = trace_id or _trace_id.get() or uuid.uuid4().hex
    current_correlation = (
        correlation_id
        or _correlation_id.get()
        or request_id
        or current_trace
    )
    current_task = task_id or _task_id.get()
    set_observability_context(
        trace_id=current_trace,
        correlation_id=current_correlation,
        task_id=current_task,
    )
    context = {
        "trace_id": current_trace,
        "correlation_id": current_correlation,
        "request_id": request_id or current_correlation,
    }
    if current_task:
        context["task_id"] = current_task
    return context


def clear_observability_context() -> None:
    """Clear the current trace context."""
    _trace_id.set("")
    _correlation_id.set("")
    _task_id.set("")


def get_log_context() -> dict[str, str]:
    """Return the current contextvars-backed tracing context."""
    context = {
        "trace_id": _trace_id.get(),
        "correlation_id": _correlation_id.get(),
        "task_id": _task_id.get(),
    }
    return {key: value for key, value in context.items() if value}


def context_from_payload(payload: dict[str, Any] | None = None) -> dict[str, str]:
    """Extract reserved trace fields from a payload dict."""
    payload = payload or {}
    meta = payload.get("_meta", {}) if isinstance(payload.get("_meta", {}), dict) else {}
    trace_id = str(meta.get("trace_id", payload.get("trace_id", "")) or "").strip()
    correlation_id = str(
        meta.get("correlation_id", payload.get("correlation_id", payload.get("request_id", ""))) or ""
    ).strip()
    task_id = str(meta.get("task_id", payload.get("task_id", "")) or "").strip()
    request_id = str(meta.get("request_id", payload.get("request_id", "")) or "").strip()
    return {
        "trace_id": trace_id,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "request_id": request_id,
    }


def bind_context_from_payload(payload: dict[str, Any] | None = None) -> dict[str, str]:
    """Bind contextvars from payload metadata, generating values when needed."""
    extracted = context_from_payload(payload)
    return ensure_trace_context(
        trace_id=extracted.get("trace_id", ""),
        correlation_id=extracted.get("correlation_id", ""),
        task_id=extracted.get("task_id", ""),
        request_id=extracted.get("request_id", ""),
    )


def enrich_payload_with_context(
    payload: dict[str, Any] | None = None,
    *,
    task_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Attach reserved `_meta` trace fields to a payload dict."""
    base = dict(payload or {})
    context = ensure_trace_context(task_id=task_id, request_id=request_id)
    existing_meta = base.get("_meta", {}) if isinstance(base.get("_meta", {}), dict) else {}
    base["_meta"] = {
        **existing_meta,
        **context,
    }
    return base


def _metrics_port_for(service_name: str) -> int:
    env_key = f"{service_name.upper().replace('-', '_')}_METRICS_PORT"
    if os.getenv(env_key):
        return int(os.getenv(env_key, "0"))
    return _DEFAULT_METRICS_PORTS.get(service_name, config.observability.metrics_port_base)


def configure_service_observability(
    service_name: str,
    *,
    start_metrics_server_for_service: bool = True,
) -> None:
    """Initialize shared observability for a service once per process."""
    global _SENTRY_INITIALIZED

    if service_name in _INITIALIZED_SERVICES:
        return
    _INITIALIZED_SERVICES.add(service_name)

    if (
        config.observability.sentry_dsn
        and sentry_sdk is not None
        and not _SENTRY_INITIALIZED
    ):
        sentry_sdk.init(
            dsn=config.observability.sentry_dsn,
            environment=config.observability.environment,
            release=config.observability.release,
            traces_sample_rate=config.observability.sentry_traces_sample_rate,
            profiles_sample_rate=config.observability.sentry_profiles_sample_rate,
            attach_stacktrace=True,
        )
        _SENTRY_INITIALIZED = True

    if (
        start_metrics_server_for_service
        and config.observability.metrics_enabled
        and start_http_server is not None
        and _enabled_in_tests()
        and service_name not in _METRICS_SERVERS
    ):
        port = _metrics_port_for(service_name)
        if port > 0:
            try:
                start_http_server(port, addr=config.observability.metrics_host)
                _METRICS_SERVERS.add(service_name)
                logger.info(
                    "Prometheus metrics server started for %s on %s:%d",
                    service_name,
                    config.observability.metrics_host,
                    port,
                )
            except OSError:
                logger.warning(
                    "Prometheus metrics server for %s could not bind to port %d",
                    service_name,
                    port,
                    exc_info=True,
                )


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop, service_name: str) -> None:
    """Capture otherwise-unhandled asyncio loop exceptions."""
    marker = f"_objective_hertz_asyncio_handler_{service_name}"
    if getattr(loop, marker, False):
        return

    previous_handler = loop.get_exception_handler()

    def _handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if exc is not None:
            capture_exception(exc, service_name=service_name, category="asyncio")
        else:
            if EXCEPTIONS is not None:
                EXCEPTIONS.labels(service=service_name, category="asyncio").inc()
            logger.error(
                "Unhandled asyncio exception in %s: %s",
                service_name,
                context.get("message", "unknown asyncio error"),
            )
        if previous_handler is not None:
            previous_handler(current_loop, context)

    loop.set_exception_handler(_handler)
    setattr(loop, marker, True)


def capture_exception(
    exc: BaseException,
    *,
    service_name: str,
    category: str = "runtime",
    extra_context: dict[str, Any] | None = None,
) -> None:
    """Send an exception to metrics and Sentry when enabled."""
    if EXCEPTIONS is not None:
        EXCEPTIONS.labels(service=service_name, category=category).inc()

    if sentry_sdk is not None and _SENTRY_INITIALIZED:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("service", service_name)
            scope.set_tag("category", category)
            for key, value in get_log_context().items():
                scope.set_tag(key, value)
            if extra_context:
                for key, value in extra_context.items():
                    scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)


def render_prometheus_metrics() -> bytes:
    """Return Prometheus exposition data for the current process."""
    return generate_latest()


def prometheus_content_type() -> str:
    """Return the appropriate content type for Prometheus metrics."""
    return CONTENT_TYPE_LATEST


def record_agent_started(agent: str) -> None:
    if AGENT_STARTS is not None:
        AGENT_STARTS.labels(agent=agent).inc()


def record_agent_shutdown(agent: str) -> None:
    if AGENT_SHUTDOWNS is not None:
        AGENT_SHUTDOWNS.labels(agent=agent).inc()


def record_event_emitted(agent: str, event_type: str) -> None:
    if EVENTS_EMITTED is not None:
        EVENTS_EMITTED.labels(agent=agent, event_type=event_type).inc()


def record_task_claimed(agent: str) -> None:
    if TASK_CLAIMS is not None:
        TASK_CLAIMS.labels(agent=agent).inc()


def record_task_completed(agent: str) -> None:
    if TASK_COMPLETIONS is not None:
        TASK_COMPLETIONS.labels(agent=agent).inc()


def record_task_failed(agent: str) -> None:
    if TASK_FAILURES is not None:
        TASK_FAILURES.labels(agent=agent).inc()


def set_active_work(agent: str, active_count: int) -> None:
    if ACTIVE_WORK is not None:
        ACTIVE_WORK.labels(agent=agent).set(active_count)


def observe_work_duration(agent: str, work_kind: str, duration_seconds: float) -> None:
    if WORK_DURATION is not None:
        WORK_DURATION.labels(agent=agent, work_kind=work_kind).observe(duration_seconds)


def observe_db_query(operation: str, duration_seconds: float, *, success: bool) -> None:
    if DB_QUERY_DURATION is not None:
        DB_QUERY_DURATION.labels(
            operation=operation.lower(),
            success="true" if success else "false",
        ).observe(duration_seconds)


def time_call_started() -> float:
    """Small helper to keep timing callsites consistent."""
    return time.perf_counter()
