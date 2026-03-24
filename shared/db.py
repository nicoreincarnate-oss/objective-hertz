"""
Postgres database helpers for Perseus.
Async connection pool using psycopg (v3, async-native).
"""

import logging
from typing import Any, Optional
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from shared.config import config

logger = logging.getLogger("perseus.db")

_pool: Optional[AsyncConnectionPool] = None


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
    async with get_conn() as conn:
        await conn.execute(query, params)


async def fetch_one(query: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    """Fetch a single row."""
    async with get_conn() as conn:
        cursor = await conn.execute(query, params)
        return await cursor.fetchone()


async def fetch_all(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Fetch all rows."""
    async with get_conn() as conn:
        cursor = await conn.execute(query, params)
        return await cursor.fetchall()


async def fetch_val(query: str, params: tuple = ()) -> Any:
    """Fetch a single value."""
    row = await fetch_one(query, params)
    if row:
        return list(row.values())[0]
    return None


# ── Convenience helpers for common operations ──

async def insert_task(
    task_type: str,
    payload: dict = None,
    priority: int = 5,
    dedupe: bool = True,
    risk_level: str = "low",
) -> int | None:
    """Insert a task into the queue.

    Scheduled recurring work should dedupe by task type to avoid runaway spend.
    Daemon-to-daemon requests should set ``dedupe=False`` so distinct tasks do not
    collapse into one another.

    risk_level: "none", "low", "medium", "high", "critical" — used by the risk gate
    to decide if the task needs human approval before execution.
    """
    import json
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
        """INSERT INTO task_queue (task_type, payload, priority, risk_level)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (task_type, json.dumps(payload or {}), priority, risk_level),
    )
    return row["id"]


async def emit_event(event_type: str, payload: dict = None) -> int:
    """Emit an event for Hermes/dashboard. Returns event ID."""
    import json
    row = await fetch_one(
        """INSERT INTO events (event_type, payload)
           VALUES (%s, %s) RETURNING id""",
        (event_type, json.dumps(payload or {})),
    )
    return row["id"]


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
