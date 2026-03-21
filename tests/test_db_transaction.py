"""Regression tests for the shared DB transaction helper."""

import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager


def run(coro):
    return asyncio.run(coro)


def load_db_module():
    fake_psycopg = types.ModuleType("psycopg")
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = object()
    fake_pool = types.ModuleType("psycopg_pool")

    class FakeAsyncConnectionPool:
        pass

    fake_pool.AsyncConnectionPool = FakeAsyncConnectionPool

    sys.modules.pop("shared.db", None)
    sys.modules["psycopg"] = fake_psycopg
    sys.modules["psycopg.rows"] = fake_rows
    sys.modules["psycopg_pool"] = fake_pool
    return importlib.import_module("shared.db")


def test_transaction_uses_connection_transaction_context():
    db = load_db_module()
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            calls.append("enter_tx")
            return None

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("exit_tx")

    class FakeConn:
        def transaction(self):
            calls.append("transaction")
            return FakeTransaction()

    @asynccontextmanager
    async def fake_get_conn():
        calls.append("enter_conn")
        yield FakeConn()
        calls.append("exit_conn")

    db.get_conn = fake_get_conn

    async def scenario():
        async with db.transaction():
            calls.append("inside")

    run(scenario())

    assert calls == ["enter_conn", "transaction", "enter_tx", "inside", "exit_tx", "exit_conn"]
