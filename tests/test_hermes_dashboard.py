"""Tests for the Hermes operator dashboard presentation."""

import importlib
import sys
import types
from datetime import datetime

from fastapi.testclient import TestClient


def test_dashboard_renders_operator_briefing_surface(monkeypatch):
    fake_db_module = types.ModuleType("shared.db")

    async def fake_fetch_all(query: str):
        if "FROM clients" in query and "GROUP BY status" in query:
            return [
                {"status": "discovered", "count": 42},
                {"status": "interested", "count": 7},
                {"status": "closed", "count": 3},
            ]
        if "FROM events" in query:
            return [
                {
                    "event_type": "deal_closed",
                    "payload": {"client": "North Coast Dental", "amount": 299},
                    "created_at": datetime(2026, 3, 21, 9, 30),
                },
                {
                    "event_type": "review_needed",
                    "payload": {"count": 2, "reason": "proposal approvals"},
                    "created_at": datetime(2026, 3, 21, 8, 15),
                },
            ]
        raise AssertionError(f"Unexpected query: {query}")

    async def fake_fetch_val(query: str):
        if "SUM(amount)" in query and "paid" in query:
            return 897
        if "SUM(amount)" in query and "pending" in query:
            return 598
        if "SUM(emails_sent)" in query and "CURRENT_DATE" in query and "- 7" not in query:
            return 247
        if "SUM(emails_sent)" in query and "CURRENT_DATE - 7" in query:
            return 1482
        if "COUNT(*) FROM clients WHERE status = 'interested'" in query:
            return 7
        if "COUNT(*) FROM clients WHERE status IN" in query:
            return 3
        if "COUNT(*) FROM clients" in query:
            return 1847
        if "COUNT(*) FROM review_queue" in query:
            return 2
        raise AssertionError(f"Unexpected query: {query}")

    async def fake_get_config(key: str, default):
        values = {
            "review_mode": True,
            "sales_completed": 3,
        }
        return values.get(key, default)

    async def fake_insert_task(task_type: str, payload: dict = None, priority: int = 5, dedupe: bool = True):
        return 1

    async def fake_emit_event(event_type: str, payload: dict = None):
        return 1

    fake_db_module.fetch_all = fake_fetch_all
    fake_db_module.fetch_one = None
    fake_db_module.fetch_val = fake_fetch_val
    fake_db_module.get_config = fake_get_config
    fake_db_module.insert_task = fake_insert_task
    fake_db_module.emit_event = fake_emit_event

    monkeypatch.setitem(sys.modules, "shared.db", fake_db_module)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)

    try:
        web_app = importlib.import_module("hermes.web.app")

        client = TestClient(web_app.app)
        response = client.get("/")

        assert response.status_code == 200
        body = response.text

        assert "War Room" in body
        assert "Operator Brief" in body
        assert "Priority Queue" in body
        assert "Signal Ledger" in body
        assert "North Coast Dental" in body
        assert "2 approvals are waiting" in body
    finally:
        sys.modules.pop("hermes.web.app", None)
