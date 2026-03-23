"""Tests for operator-to-agent messaging from the Hermes dashboard."""

import importlib
import sys
import types
from datetime import datetime

from fastapi.testclient import TestClient


class FakeDashboardDB(types.ModuleType):
    def __init__(self):
        super().__init__("shared.db")
        self.tasks: list[dict] = []
        self.events: list[dict] = []

    async def fetch_all(self, query: str, params: tuple = ()):
        if "FROM clients" in query and "GROUP BY status" in query:
            return [
                {"status": "discovered", "count": 42},
                {"status": "interested", "count": 7},
                {"status": "closed", "count": 3},
            ]
        if "FROM events" in query:
            return [
                {
                    "event_type": "agent_message_ack",
                    "payload": {
                        "agent": "titan",
                        "reply": "Titan received your note and queued it for the next cycle.",
                        "operator_message": "Focus on hot leads first.",
                    },
                    "created_at": datetime(2026, 3, 21, 9, 30),
                },
                {
                    "event_type": "deal_closed",
                    "payload": {"client": "North Coast Dental", "amount": 299},
                    "created_at": datetime(2026, 3, 21, 8, 15),
                },
            ]
        raise AssertionError(f"Unexpected query: {query} {params}")

    async def fetch_val(self, query: str, params: tuple = ()):
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
        raise AssertionError(f"Unexpected query: {query} {params}")

    async def get_config(self, key: str, default=None):
        values = {
            "review_mode": True,
            "sales_completed": 3,
        }
        return values.get(key, default)

    async def insert_task(self, task_type: str, payload: dict = None, priority: int = 5, dedupe: bool = True):
        task_id = len(self.tasks) + 1
        self.tasks.append(
            {
                "id": task_id,
                "task_type": task_type,
                "payload": payload or {},
                "priority": priority,
                "dedupe": dedupe,
            }
        )
        return task_id

    async def emit_event(self, event_type: str, payload: dict = None):
        event_id = len(self.events) + 1
        self.events.append({"id": event_id, "event_type": event_type, "payload": payload or {}})
        return event_id


def _load_web_app(monkeypatch):
    fake_db = FakeDashboardDB()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.presenter", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.operator_chat", raising=False)
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)
    app_module = importlib.import_module("hermes.web.app")
    return app_module, fake_db


def test_dashboard_renders_agent_link_panel(monkeypatch):
    web_app, _ = _load_web_app(monkeypatch)

    try:
        client = TestClient(web_app.app)
        response = client.get("/")

        assert response.status_code == 200
        body = response.text

        assert "Agent Link" in body
        assert "Talk to an agent" in body
        assert "Focus on hot leads first." in body
        assert "Titan received your note" in body
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_operator_chat_post_queues_agent_message(monkeypatch):
    web_app, fake_db = _load_web_app(monkeypatch)

    try:
        client = TestClient(web_app.app)
        response = client.post(
            "/api/operator-chat",
            data={
                "target_agent": "titan",
                "priority": "urgent",
                "message": "Pause low-value outreach and work the hot leads first.",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/?operator_sent=1")

        assert fake_db.tasks == [
            {
                "id": 1,
                "task_type": "titan_operator_message",
                "payload": {
                    "target_agent": "titan",
                    "message": "Pause low-value outreach and work the hot leads first.",
                    "priority": "urgent",
                    "source": "war_room",
                },
                "priority": 1,
                "dedupe": False,
            }
        ]
        assert fake_db.events == [
            {
                "id": 1,
                "event_type": "operator_message_sent",
                "payload": {
                    "target_agent": "titan",
                    "message": "Pause low-value outreach and work the hot leads first.",
                    "priority": "urgent",
                    "task_id": 1,
                    "source": "war_room",
                },
            }
        ]
    finally:
        sys.modules.pop("hermes.web.app", None)
