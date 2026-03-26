import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_app(tmp_path: Path):
    os.environ["MEM0_HISTORY_DB_PATH"] = str(tmp_path / "history.db")
    sys.modules.pop("tools.mem0_compat_service", None)
    module = importlib.import_module("tools.mem0_compat_service")
    return module


def test_mem0_compat_store_search_delete_and_health(tmp_path):
    module = _load_app(tmp_path)
    client = TestClient(module.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    created = client.post(
        "/v1/memories/",
        json={
            "messages": [{"role": "assistant", "content": "Smith Dental prefers Tuesday outreach"}],
            "user_id": "client:42",
            "metadata": {"category": "email", "client_id": 42},
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["id"]

    results = client.post(
        "/v1/memories/search/",
        json={"query": "Tuesday outreach for dental", "user_id": "client:42", "limit": 5},
    )
    assert results.status_code == 200
    payload = results.json()
    assert payload["results"]
    assert payload["results"][0]["id"] == memory_id
    assert payload["results"][0]["metadata"]["category"] == "email"

    deleted = client.delete(f"/v1/memories/{memory_id}/", params={"user_id": "client:42"})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    after_delete = client.post(
        "/v1/memories/search/",
        json={"query": "Tuesday outreach for dental", "user_id": "client:42", "limit": 5},
    )
    assert after_delete.status_code == 200
    assert after_delete.json()["results"] == []
