"""Tests for the Hermes health API contract consumed by the frontend."""

import importlib
import sys
import types

from fastapi.testclient import TestClient


def test_health_api_includes_mode_and_metrics(monkeypatch):
    fake_db_module = types.ModuleType("shared.db")

    async def fake_fetch_val(query: str, params: tuple = ()):
        if query == "SELECT 1":
            return 1
        if "SUM(emails_sent)" in query and "CURRENT_DATE - 7" not in query:
            return 247
        if "SUM(emails_sent)" in query and "CURRENT_DATE - 7" in query:
            return 1482
        if "COUNT(*) FROM clients WHERE status = 'interested'" in query:
            return 23
        if "COUNT(*) FROM clients WHERE status IN" in query:
            return 3
        if "COUNT(*) FROM review_queue" in query:
            return 2
        if "SUM(amount)" in query and "status = 'paid'" in query:
            return 897.00
        if "SUM(amount)" in query and "status = 'pending'" in query:
            return 299.00
        if "COUNT(*) FROM clients" in query and "status" not in query:
            return 1247
        raise AssertionError(f"Unexpected query: {query} {params}")

    async def fake_fetch_all(query: str, params: tuple = ()):
        return []

    async def fake_get_config(key: str, default=None):
        if key == "review_mode":
            return True
        return default

    async def fake_insert_task(task_type: str, payload: dict = None, priority: int = 5, dedupe: bool = True):
        return 1

    async def fake_emit_event(event_type: str, payload: dict = None):
        return 1

    async def fake_init_pool(*args, **kwargs):
        return None

    async def fake_close_pool():
        return None

    fake_db_module.fetch_val = fake_fetch_val
    fake_db_module.fetch_all = fake_fetch_all
    fake_db_module.fetch_one = None
    fake_db_module.get_config = fake_get_config
    fake_db_module.insert_task = fake_insert_task
    fake_db_module.emit_event = fake_emit_event
    fake_db_module.init_pool = fake_init_pool
    fake_db_module.close_pool = fake_close_pool

    fake_registry_module = types.ModuleType("perseus.agent_registry")

    async def fake_check_agent_health():
        return {
            "perseus": "ok",
            "titan": "ok",
            "hermes": "ok",
            "clawdbot": "ok",
        }

    fake_registry_module.check_agent_health = fake_check_agent_health

    monkeypatch.setitem(sys.modules, "shared.db", fake_db_module)
    monkeypatch.setitem(sys.modules, "perseus.agent_registry", fake_registry_module)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)

    try:
        web_app = importlib.import_module("hermes.web.app")
        client = TestClient(web_app.app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "db_ok": True,
            "db_error": "",
            "agents": {
                "perseus": "ok",
                "titan": "ok",
                "hermes": "ok",
                "clawdbot": "ok",
            },
            "mode": "review",
            "metrics": {
                "emails_sent_today": 247,
                "emails_sent_week": 1482,
                "warm_leads": 23,
                "sales_closed": 3,
                "pending_approvals": 2,
                "revenue_cleared": 897.0,
                "revenue_pending": 299.0,
                "total_leads": 1247,
            },
        }
    finally:
        sys.modules.pop("hermes.web.app", None)
