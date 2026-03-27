"""Tests for the Hermes strategic insights API auth and grounding."""

import importlib
import sys
import types

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

from fastapi.testclient import TestClient

TEST_SECRET = "test-dashboard-secret-42"


class FakeInsightsDB(types.ModuleType):
    def __init__(self):
        super().__init__("shared.db")

    async def fetch_all(self, query: str, params: tuple = ()):
        if "FROM clients" in query and "GROUP BY industry" in query:
            return [
                {"industry": "restaurant", "total_leads": 12, "interested": 4, "closed": 3, "avg_lead_score": 82.5},
                {"industry": "salon", "total_leads": 10, "interested": 1, "closed": 0, "avg_lead_score": 61.0},
            ]
        if "FROM titan_learnings" in query:
            return [{"category": "email_performance", "insight": "Restaurants replied more when the email mentioned delivery.", "confidence": 0.82}]
        return []

    async def fetch_val(self, query: str, params: tuple = ()):
        if "SELECT 1" in query:
            return 1
        return 0

    async def get_config(self, key: str, default=None):
        return default

    async def insert_task(self, task_type, payload=None, priority=5, dedupe=True):
        return 1

    async def emit_event(self, event_type, payload=None):
        return 1

    async def init_pool(self):
        pass

    async def close_pool(self):
        pass


class FakeLLM(types.ModuleType):
    def __init__(self):
        super().__init__("shared.llm_client")
        self.calls = []
        self.llm = self

    async def generate(self, prompt, model="smart", temperature=0.2, max_tokens=0, **kwargs):
        self.calls.append({"prompt": prompt})
        return (
            '{"answer":"Restaurants outperformed salons because they had stronger digital-intent signals.",'
            '"evidence":["Restaurants had 3 closes from 12 leads."],'
            '"recommended_actions":["Double down on restaurant outreach."]}'
        )


def _load_web_app(monkeypatch):
    fake_db = FakeInsightsDB()
    fake_llm = FakeLLM()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.setitem(sys.modules, "shared.llm_client", fake_llm)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.insights", raising=False)
    monkeypatch.setenv("DASHBOARD_SECRET", TEST_SECRET)
    app_module = importlib.import_module("hermes.web.app")
    return app_module, fake_llm


def test_insights_requires_auth(monkeypatch):
    """POST /api/insights must require authentication."""
    web_app, _ = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.post("/api/insights", json={"question": "test"})
        assert response.status_code in (401, 303), f"Expected auth rejection, got {response.status_code}"
    finally:
        sys.modules.pop("hermes.web.app", None)


def test_strategic_insights_api_answers_grounded_question(monkeypatch):
    """Authenticated POST /api/insights must return grounded answer."""
    web_app, fake_llm = _load_web_app(monkeypatch)
    try:
        client = TestClient(web_app.app, raise_server_exceptions=False)
        response = client.post(
            "/api/insights",
            json={"question": "Why did restaurants convert better than salons?"},
            headers={"Authorization": f"Bearer {TEST_SECRET}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"].startswith("Restaurants outperformed salons")
        assert len(payload["evidence"]) >= 1
        assert len(payload["recommended_actions"]) >= 1
    finally:
        sys.modules.pop("hermes.web.app", None)
