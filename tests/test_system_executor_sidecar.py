"""Regression tests for the system executor donor sidecar integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_peekaboo_adapter_uses_configured_base_url(monkeypatch):
    monkeypatch.setenv("SYSTEM_EXECUTOR_PEEKABOO_URL", "http://peekaboo.local/")

    from system_executor.adapters import PeekabooAdapter

    adapter = PeekabooAdapter()
    assert adapter.base_url == "http://peekaboo.local"
    assert adapter.available() is True


def test_remote_adapter_health_falls_back_to_a2a_health_endpoint():
    from system_executor.adapters import RemoteJSONAdapter

    adapter = RemoteJSONAdapter("peekaboo", "http://donor.local")

    first = MagicMock(status_code=503)
    second = MagicMock(status_code=200)

    with patch("httpx.get", side_effect=[first, second]) as mock_get:
        result = adapter.health()

    assert result["status"] == "ok"
    assert result["endpoint"] == "/a2a/health"
    assert mock_get.call_args_list[0].args[0] == "http://donor.local/health"
    assert mock_get.call_args_list[1].args[0] == "http://donor.local/a2a/health"


def test_system_executor_runtime_honors_backend_order(monkeypatch):
    monkeypatch.setenv("SYSTEM_EXECUTOR_BACKENDS", "cua,peekaboo,local")

    from system_executor.adapters import CuaAdapter, LocalAdapter, PeekabooAdapter, SystemExecutorRuntime

    peekaboo = PeekabooAdapter()
    cua = CuaAdapter()
    local = LocalAdapter()

    peekaboo.available = lambda: True
    cua.available = lambda: False
    local.available = lambda: True

    runtime = SystemExecutorRuntime(adapters=[cua, peekaboo, local])
    assert runtime.active_backend("desktop_exec") == "local"
    assert runtime.active_backend("health_check") == "peekaboo"


def test_system_executor_status_reports_preferred_and_selected_backend(monkeypatch):
    monkeypatch.setenv("SYSTEM_EXECUTOR_BACKENDS", "cua,peekaboo,local")

    from system_executor.adapters import CuaAdapter, LocalAdapter, PeekabooAdapter, SystemExecutorRuntime

    peekaboo = PeekabooAdapter()
    cua = CuaAdapter()
    local = LocalAdapter()

    peekaboo.available = lambda: True
    peekaboo.ready = lambda: False
    cua.available = lambda: False
    local.available = lambda: True

    runtime = SystemExecutorRuntime(adapters=[cua, peekaboo, local])

    import asyncio

    status = asyncio.run(runtime.status("desktop_control"))
    assert status["preferred_backend"] == "peekaboo"
    assert status["selected_backend"] == "local"
    assert status["fallback_backend"] == "local"
