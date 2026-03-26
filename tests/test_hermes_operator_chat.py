"""Tests for operator-to-agent messaging auth and dispatch."""

import importlib
import sys
import types
from datetime import datetime

# Ensure python-multipart stub exists for Starlette/FastAPI if not installed.
try:
    import multipart.multipart  # noqa: F401
except (ImportError, ModuleNotFoundError):
    _mp = types.ModuleType("multipart")
    _mp.__version__ = "0.0.18"  # type: ignore[attr-defined]
    _mpm = types.ModuleType("multipart.multipart")
    _mpm.parse_options_header = lambda *a, **k: (b"", {})  # type: ignore[attr-defined]
    _mpm.MultipartParser = type("MultipartParser", (), {})  # type: ignore[attr-defined]
    _mpm.QuerystringParser = type("QuerystringParser", (), {})  # type: ignore[attr-defined]
    _mpm.DecodeError = Exception  # type: ignore[attr-defined]
    _mpm.MultipartParseError = Exception  # type: ignore[attr-defined]
    _mp.multipart = _mpm  # type: ignore[attr-defined]
    sys.modules["multipart"] = _mp
    sys.modules["multipart.multipart"] = _mpm
    _mpe = types.ModuleType("multipart.exceptions")
    _mpe.DecodeError = Exception  # type: ignore[attr-defined]
    _mpe.MultipartParseError = Exception  # type: ignore[attr-defined]
    sys.modules["multipart.exceptions"] = _mpe

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-dashboard-secret-42"


def _has_real_multipart() -> bool:
    """Check if the real python-multipart is installed (not our stub)."""
    try:
        import multipart
        poh = getattr(multipart.multipart, "parse_options_header", None)
        # Our stub uses a lambda; the real one is a named function
        return poh is not None and "<lambda>" not in getattr(poh, "__qualname__", "<lambda>")
    except Exception:
        return False


class FakeDashboardDB(types.ModuleType):
    def __init__(self):
        super().__init__("shared.db")
        self.tasks: list[dict] = []
        self.events: list[dict] = []

    async def fetch_all(self, query: str, params: tuple = ()):
        if "FROM clients" in query and "GROUP BY status" in query:
            return [{"status": "discovered", "count": 42}]
        if "FROM events" in query:
            return [
                {
                    "event_type": "agent_message_ack",
                    "payload": {
                        "agent": "titan",
                        "reply": "Titan received your note.",
                        "operator_message": "Focus on hot leads first.",
                    },
                    "created_at": datetime(2026, 3, 21, 9, 30),
                },
            ]
        return []

    async def fetch_val(self, query: str, params: tuple = ()):
        if "SELECT 1" in query:
            return 1
        return 0

    async def get_config(self, key: str, default=None):
        return {"review_mode": True, "sales_completed": 3}.get(key, default)

    async def insert_task(self, task_type: str, payload: dict = None, priority: int = 5, dedupe: bool = True):
        task_id = len(self.tasks) + 1
        self.tasks.append({
            "id": task_id, "task_type": task_type,
            "payload": payload or {}, "priority": priority, "dedupe": dedupe,
        })
        return task_id

    async def emit_event(self, event_type: str, payload: dict = None):
        event_id = len(self.events) + 1
        self.events.append({"id": event_id, "event_type": event_type, "payload": payload or {}})
        return event_id

    async def init_pool(self):
        pass

    async def close_pool(self):
        pass


def _load_web_app(monkeypatch):
    fake_db = FakeDashboardDB()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.presenter", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.operator_chat", raising=False)
    monkeypatch.setenv("DASHBOARD_SECRET", TEST_SECRET)
    app_module = importlib.import_module("hermes.web.app")
    return app_module, fake_db


def test_operator_chat_requires_auth(monkeypatch):
    """POST /api/operator-chat must require authentication."""
    web_app, _ = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.post(
            "/api/operator-chat",
            data={"target_agent": "titan", "priority": "urgent", "message": "test"},
            follow_redirects=False,
        )
        assert response.status_code in (401, 303), f"Expected auth rejection, got {response.status_code}"
    finally:
        sys.modules.pop("hermes.web.app", None)


@pytest.mark.skipif(
    not _has_real_multipart(),
    reason="python-multipart not installed — Form() parsing unavailable",
)
def test_operator_chat_post_queues_agent_message(monkeypatch):
    """Authenticated POST /api/operator-chat must queue a task and emit an event."""
    web_app, fake_db = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.post(
            "/api/operator-chat",
            content="target_agent=titan&priority=urgent&message=Pause+low-value+outreach.",
            headers={
                "Authorization": f"Bearer {TEST_SECRET}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/?operator_sent=1")

        assert len(fake_db.tasks) == 1
        assert fake_db.tasks[0]["task_type"] == "titan_operator_message"
        assert fake_db.tasks[0]["payload"]["message"] == "Pause low-value outreach."

        assert len(fake_db.events) == 1
        assert fake_db.events[0]["event_type"] == "operator_message_sent"
    finally:
        sys.modules.pop("hermes.web.app", None)
