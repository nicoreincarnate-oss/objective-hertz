"""Tests for the Hermes operator dashboard auth and presentation."""

import importlib
import sys
import types
from datetime import datetime

# Ensure python-multipart stub exists for Starlette/FastAPI if not installed.
# FastAPI checks `multipart.__version__` and `multipart.multipart.parse_options_header`.
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

from fastapi.testclient import TestClient

TEST_SECRET = "test-dashboard-secret-42"


def _build_fake_db():
    fake_db = types.ModuleType("shared.db")

    async def fake_fetch_all(query: str, params=None):
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
            ]
        return []

    async def fake_fetch_val(query: str, params=None):
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
        if "SELECT 1" in query:
            return 1
        return 0

    async def fake_get_config(key: str, default=None):
        return {"review_mode": True, "sales_completed": 3}.get(key, default)

    async def fake_insert_task(task_type, payload=None, priority=5, dedupe=True):
        return 1

    async def fake_emit_event(event_type, payload=None):
        return 1

    async def noop_init():
        pass

    fake_db.fetch_all = fake_fetch_all
    fake_db.fetch_one = None
    fake_db.fetch_val = fake_fetch_val
    fake_db.get_config = fake_get_config
    fake_db.insert_task = fake_insert_task
    fake_db.emit_event = fake_emit_event
    fake_db.init_pool = noop_init
    fake_db.close_pool = noop_init
    return fake_db


def _load_web_app(monkeypatch):
    fake_db = _build_fake_db()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.presenter", raising=False)
    monkeypatch.setenv("DASHBOARD_SECRET", TEST_SECRET)
    app_module = importlib.import_module("hermes.web.app")
    return app_module


# ── Auth boundary tests ──


def test_health_requires_auth(monkeypatch):
    """GET /api/health must require authentication (not public)."""
    web_app = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.get("/api/health")
        # Should get 401 or redirect to login, NOT 200
        assert response.status_code in (401, 303), f"Expected 401/303, got {response.status_code}"
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_health_accessible_with_bearer(monkeypatch):
    """GET /api/health with valid Bearer token must return telemetry."""
    web_app = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.get("/api/health", headers={"Authorization": f"Bearer {TEST_SECRET}"})
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert "revenue_cleared" in data["metrics"]
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_liveness_is_public(monkeypatch):
    """GET /api/liveness must be public and return only status/db_ok."""
    web_app = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.get("/api/liveness")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "db_ok" in data
        # Must NOT leak business data
        assert "metrics" not in data
        assert "revenue" not in str(data).lower()
        assert "leads" not in str(data).lower()
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_protected_routes_reject_without_auth(monkeypatch):
    """Protected routes must reject unauthenticated requests."""
    web_app = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        protected = ["/", "/api/pipeline", "/api/leads", "/api/events"]
        for path in protected:
            response = client.get(path)
            assert response.status_code in (401, 303), f"{path} returned {response.status_code} without auth"
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_no_secret_blocks_everything(monkeypatch):
    """When DASHBOARD_SECRET is not set, all non-public routes return 503."""
    fake_db = _build_fake_db()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.presenter", raising=False)
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)
    app_module = importlib.import_module("hermes.web.app")
    try:
        client = TestClient(app_module.app, raise_server_exceptions=False)
        response = client.get("/")
        assert response.status_code == 503
    finally:
        sys.modules.pop("hermes.web.app", None)
