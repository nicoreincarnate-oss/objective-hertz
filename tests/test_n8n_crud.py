"""Tests for N8N client CRUD methods.

Mocks the HTTP layer so tests run without a live N8N instance.
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Ensure auth env vars are set for tests
os.environ.setdefault("N8N_USER", "test_user")
os.environ.setdefault("N8N_PASSWORD", "test_pass")

from tools.n8n_client import (
    activate_workflow,
    create_workflow,
    deactivate_workflow,
    delete_workflow,
    export_workflow_to_json,
    get_workflow,
    import_workflow_from_json,
    update_workflow,
)

# ── Fixtures ─────────────────────────────────────────────────────

SAMPLE_WORKFLOW = {
    "id": "42",
    "name": "Test Workflow",
    "active": False,
    "nodes": [],
    "connections": {},
}

SAMPLE_DEFINITION = {
    "name": "New Workflow",
    "nodes": [{"type": "n8n-nodes-base.start", "name": "Start"}],
    "connections": {},
}


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("GET", "http://test"),
    )
    return resp


# ── Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_workflow():
    """POST /api/v1/workflows returns the new workflow ID."""
    created = {**SAMPLE_DEFINITION, "id": "99"}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_response(201, created))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        wf_id = await create_workflow(SAMPLE_DEFINITION)

    assert wf_id == "99"
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "/api/v1/workflows" in call_kwargs.args[0]
    assert call_kwargs.kwargs["json"] == SAMPLE_DEFINITION


@pytest.mark.asyncio
async def test_get_workflow():
    """GET /api/v1/workflows/{id} returns the workflow."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response(200, SAMPLE_WORKFLOW))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_workflow("42")

    assert result["ok"] is True
    assert result["workflow"]["id"] == "42"
    assert result["workflow"]["name"] == "Test Workflow"


@pytest.mark.asyncio
async def test_update_workflow():
    """PUT /api/v1/workflows/{id} returns the updated workflow."""
    updated = {**SAMPLE_WORKFLOW, "name": "Updated Workflow"}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.put = AsyncMock(return_value=_mock_response(200, updated))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        result = await update_workflow("42", {"name": "Updated Workflow"})

    assert result["ok"] is True
    assert result["workflow"]["name"] == "Updated Workflow"


@pytest.mark.asyncio
async def test_delete_workflow():
    """DELETE /api/v1/workflows/{id} returns True on success."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.delete = AsyncMock(return_value=_mock_response(200))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        ok = await delete_workflow("42")

    assert ok is True


@pytest.mark.asyncio
async def test_activate_workflow():
    """PATCH /api/v1/workflows/{id} with active=True."""
    activated = {**SAMPLE_WORKFLOW, "active": True}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.patch = AsyncMock(return_value=_mock_response(200, activated))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        result = await activate_workflow("42")

    assert result["ok"] is True
    assert result["workflow"]["active"] is True
    call_kwargs = mock_client.patch.call_args
    assert call_kwargs.kwargs["json"] == {"active": True}


@pytest.mark.asyncio
async def test_deactivate_workflow():
    """PATCH /api/v1/workflows/{id} with active=False."""
    deactivated = {**SAMPLE_WORKFLOW, "active": False}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.patch = AsyncMock(return_value=_mock_response(200, deactivated))

    with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
        result = await deactivate_workflow("42")

    assert result["ok"] is True
    assert result["workflow"]["active"] is False
    call_kwargs = mock_client.patch.call_args
    assert call_kwargs.kwargs["json"] == {"active": False}


@pytest.mark.asyncio
async def test_import_from_json():
    """import_workflow_from_json reads a file and calls create_workflow."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_DEFINITION, f)
        tmp_path = f.name

    try:
        created = {**SAMPLE_DEFINITION, "id": "77"}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(201, created))

        with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
            wf_id = await import_workflow_from_json(tmp_path)

        assert wf_id == "77"
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_export_to_json():
    """export_workflow_to_json fetches a workflow and writes it to disk."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response(200, SAMPLE_WORKFLOW))

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "exported.json")

        with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
            result_path = await export_workflow_to_json("42", out_path)

        assert result_path == out_path
        with open(out_path) as f:
            data = json.load(f)
        assert data["id"] == "42"
        assert data["name"] == "Test Workflow"


@pytest.mark.asyncio
async def test_crud_no_connection():
    """All CRUD methods degrade gracefully when N8N is unreachable."""
    with patch.dict(os.environ, {"N8N_USER": "u", "N8N_PASSWORD": "p"}):
        # Simulate connection refused on all methods
        exc = httpx.ConnectError("Connection refused")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=exc)
        mock_client.post = AsyncMock(side_effect=exc)
        mock_client.put = AsyncMock(side_effect=exc)
        mock_client.delete = AsyncMock(side_effect=exc)
        mock_client.patch = AsyncMock(side_effect=exc)

        with patch("tools.n8n_client.httpx.AsyncClient", return_value=mock_client):
            assert await create_workflow(SAMPLE_DEFINITION) == ""
            result = await get_workflow("1")
            assert result["ok"] is False
            result = await update_workflow("1", {})
            assert result["ok"] is False
            assert await delete_workflow("1") is False
            result = await activate_workflow("1")
            assert result["ok"] is False
            result = await deactivate_workflow("1")
            assert result["ok"] is False
