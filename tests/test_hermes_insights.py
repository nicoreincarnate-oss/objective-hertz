"""Tests for the Hermes strategic insights surface."""

import importlib
import sys
import types

from fastapi.testclient import TestClient


class FakeInsightsDB(types.ModuleType):
    def __init__(self):
        super().__init__("shared.db")

    async def fetch_all(self, query: str, params: tuple = ()):
        if "FROM clients" in query and "GROUP BY industry" in query:
            return [
                {
                    "industry": "restaurant",
                    "total_leads": 12,
                    "interested": 4,
                    "closed": 3,
                    "avg_lead_score": 82.5,
                },
                {
                    "industry": "salon",
                    "total_leads": 10,
                    "interested": 1,
                    "closed": 0,
                    "avg_lead_score": 61.0,
                },
            ]
        if "FROM titan_learnings" in query:
            return [
                {
                    "category": "email_performance",
                    "insight": "Restaurants replied more when the email mentioned delivery and online reservations.",
                    "confidence": 0.82,
                }
            ]
        raise AssertionError(f"Unexpected query: {query} {params}")

    async def fetch_val(self, query: str, params: tuple = ()):
        if "COUNT(*) FROM review_queue" in query:
            return 0
        if "COUNT(*) FROM clients" in query and "status = 'interested'" in query:
            return 5
        if "COUNT(*) FROM clients" in query and "status IN" in query:
            return 3
        if "COUNT(*) FROM clients" in query:
            return 22
        if "SUM(amount)" in query and "paid" in query:
            return 897
        if "SUM(amount)" in query and "pending" in query:
            return 299
        if "SUM(emails_sent)" in query and "CURRENT_DATE - 7" in query:
            return 120
        if "SUM(emails_sent)" in query and "CURRENT_DATE" in query:
            return 20
        raise AssertionError(f"Unexpected query: {query} {params}")

    async def get_config(self, key: str, default=None):
        return default

    async def insert_task(self, task_type: str, payload: dict = None, priority: int = 5, dedupe: bool = True):
        return 1

    async def emit_event(self, event_type: str, payload: dict = None):
        return 1


class FakeLLM(types.ModuleType):
    def __init__(self):
        super().__init__("shared.llm_client")
        self.calls = []
        self.llm = self

    async def generate(self, prompt: str, model: str = "smart", temperature: float = 0.2, max_tokens: int = 0, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "temperature": temperature, "max_tokens": max_tokens, **kwargs})
        return (
            '{"answer":"Restaurants outperformed salons because they had stronger digital-intent signals and clearer revenue upside.",'
            '"evidence":["Restaurants had 3 closes from 12 leads.","Salons had 0 closes from 10 leads."],'
            '"recommended_actions":["Double down on restaurant outreach.","Retest salon messaging before scaling."]}'
        )


def _load_web_app(monkeypatch):
    fake_db = FakeInsightsDB()
    fake_llm = FakeLLM()
    monkeypatch.setitem(sys.modules, "shared.db", fake_db)
    monkeypatch.setitem(sys.modules, "shared.llm_client", fake_llm)
    monkeypatch.delitem(sys.modules, "hermes.web.app", raising=False)
    monkeypatch.delitem(sys.modules, "hermes.web.insights", raising=False)
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)
    app_module = importlib.import_module("hermes.web.app")
    return app_module, fake_llm


def test_strategic_insights_api_answers_grounded_question(monkeypatch):
    web_app, fake_llm = _load_web_app(monkeypatch)

    try:
        client = TestClient(web_app.app)
        response = client.post(
            "/api/insights",
            json={"question": "Why did restaurants convert better than salons?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"].startswith("Restaurants outperformed salons")
        assert payload["evidence"] == [
            "Restaurants had 3 closes from 12 leads.",
            "Salons had 0 closes from 10 leads.",
        ]
        assert payload["recommended_actions"][0] == "Double down on restaurant outreach."

        prompt = fake_llm.calls[0]["prompt"]
        assert "Why did restaurants convert better than salons?" in prompt
        assert '"industry": "restaurant"' in prompt
        assert '"industry": "salon"' in prompt
        assert "Restaurants replied more when the email mentioned delivery" in prompt
    finally:
        sys.modules.pop("hermes.web.app", None)

