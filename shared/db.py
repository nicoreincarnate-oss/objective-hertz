"""
Postgres database helpers for Perseus.
Async connection pool using psycopg (v3, async-native).
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from shared.config import config
from shared.observability import (
    capture_exception,
    enrich_payload_with_context,
    observe_db_query,
)

logger = logging.getLogger("perseus.db")

_pool: AsyncConnectionPool | None = None


async def init_pool(min_size: int = 2, max_size: int = 10):
    """Initialize the connection pool. Call once at daemon startup."""
    global _pool
    if _pool is not None:
        return
    _pool = AsyncConnectionPool(
        conninfo=config.postgres.dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
    )
    await _pool.open()
    logger.info("Postgres pool initialized (%d-%d connections)", min_size, max_size)


async def close_pool():
    """Close the connection pool. Call on daemon shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Postgres pool closed")


@asynccontextmanager
async def get_conn():
    """Get a connection from the pool."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    async with _pool.connection() as conn:
        yield conn


@asynccontextmanager
async def transaction():
    """Run multiple DB statements inside a single transaction."""
    async with get_conn() as conn:
        async with conn.transaction():
            yield conn


async def execute(query: str, params: tuple = ()) -> None:
    """Execute a query (INSERT, UPDATE, DELETE)."""
    started_at = time.perf_counter()
    operation = query.strip().split(None, 1)[0] if query.strip() else "unknown"
    async with get_conn() as conn:
        try:
            await conn.execute(query, params)
        except Exception as exc:
            observe_db_query(operation, time.perf_counter() - started_at, success=False)
            capture_exception(
                exc,
                service_name="db",
                category="query",
                extra_context={"operation": operation},
            )
            raise
        observe_db_query(operation, time.perf_counter() - started_at, success=True)


async def fetch_one(query: str, params: tuple = ()) -> dict[str, Any] | None:
    """Fetch a single row."""
    started_at = time.perf_counter()
    operation = query.strip().split(None, 1)[0] if query.strip() else "unknown"
    async with get_conn() as conn:
        try:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
        except Exception as exc:
            observe_db_query(operation, time.perf_counter() - started_at, success=False)
            capture_exception(
                exc,
                service_name="db",
                category="query",
                extra_context={"operation": operation},
            )
            raise
        observe_db_query(operation, time.perf_counter() - started_at, success=True)
        return row


async def fetch_all(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Fetch all rows."""
    started_at = time.perf_counter()
    operation = query.strip().split(None, 1)[0] if query.strip() else "unknown"
    async with get_conn() as conn:
        try:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
        except Exception as exc:
            observe_db_query(operation, time.perf_counter() - started_at, success=False)
            capture_exception(
                exc,
                service_name="db",
                category="query",
                extra_context={"operation": operation},
            )
            raise
        observe_db_query(operation, time.perf_counter() - started_at, success=True)
        return rows


async def fetch_val(query: str, params: tuple = ()) -> Any:
    """Fetch a single value."""
    row = await fetch_one(query, params)
    if row:
        return list(row.values())[0]
    return None


# ── Convenience helpers for common operations ──

async def insert_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    priority: int = 5,
    dedupe: bool = True,
) -> int | None:
    """Insert a task into the queue.

    Scheduled recurring work should dedupe by task type to avoid runaway spend.
    Daemon-to-daemon requests should set ``dedupe=False`` so distinct tasks do not
    collapse into one another.
    """
    import json
    payload = enrich_payload_with_context(payload)
    if dedupe:
        # Skip if there's already a pending or running task of this type
        existing = await fetch_one(
            """SELECT id FROM task_queue
               WHERE task_type = %s AND status IN ('pending', 'running')
               LIMIT 1""",
            (task_type,),
        )
        if existing:
            return None

    row = await fetch_one(
        """INSERT INTO task_queue (task_type, payload, priority)
           VALUES (%s, %s, %s) RETURNING id""",
        (task_type, json.dumps(payload or {}), priority),
    )
    return int(row["id"]) if row else None


async def emit_event(event_type: str, payload: dict[str, Any] | None = None) -> int:
    """Emit an event for Hermes/dashboard. Returns event ID."""
    import json
    payload = enrich_payload_with_context(payload)
    row = await fetch_one(
        """INSERT INTO events (event_type, payload)
           VALUES (%s, %s) RETURNING id""",
        (event_type, json.dumps(payload or {})),
    )
    return int(row["id"]) if row else 0


async def get_config(key: str, default: Any = None) -> Any:
    """Get a system config value. Returns the raw value from JSONB."""
    row = await fetch_one("SELECT value FROM system_config WHERE key = %s", (key,))
    if row:
        # JSONB columns return Python objects directly via psycopg
        return row["value"]
    return default


async def set_config(key: str, value: Any) -> None:
    """Set a system config value. Stores directly as JSONB."""
    from psycopg.types.json import Jsonb
    await execute(
        """INSERT INTO system_config (key, value)
           VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value,
               is_customized = TRUE,
               updated_at = NOW()""",
        (key, Jsonb(value)),
    )


async def increment_config_int(key: str, delta: int = 1, default: int = 0) -> int:
    """Atomically increment an integer system_config value and return the new value."""
    row = await fetch_one(
        """INSERT INTO system_config (key, value, is_customized)
           VALUES (%s, to_jsonb((%s)::int), TRUE)
           ON CONFLICT (key) DO UPDATE
           SET value = to_jsonb((COALESCE(system_config.value #>> '{}', %s)::int + %s)),
               is_customized = TRUE,
               updated_at = NOW()
           RETURNING (value #>> '{}')::int AS value""",
        (key, default + delta, str(default), delta),
    )
    return int(row["value"]) if row else default + delta
