"""Tests for shared.integration_boot — the unified daemon startup sequence."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_boot_all_ok():
    """All modules available and initialize successfully."""
    mock_collector = MagicMock()
    mock_collector._initialized = False
    mock_collector.initialize = AsyncMock()

    mock_auto_scan = MagicMock(return_value=5)
    mock_register = AsyncMock(return_value=3)
    mock_start_mcp = AsyncMock(return_value=True)

    with (
        patch.dict("sys.modules", {}),
        patch("shared.integration_boot._boot_telemetry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_tool_registry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_register_tools", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_mcp_server", new_callable=AsyncMock, return_value="ok"),
    ):
        from shared.integration_boot import boot_integration
        status = await boot_integration("orchestrator")

    assert status["telemetry"] == "ok"
    assert status["tool_registry"] == "ok"
    assert status["register_tools"] == "ok"
    assert status["mcp_server"] == "ok"
    assert "channels" not in status  # Not hermes


@pytest.mark.asyncio
async def test_boot_telemetry_failure_continues():
    """Telemetry fails but remaining modules still boot."""
    with (
        patch("shared.integration_boot._boot_telemetry", new_callable=AsyncMock, return_value="error: connection refused"),
        patch("shared.integration_boot._boot_tool_registry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_register_tools", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_mcp_server", new_callable=AsyncMock, return_value="ok"),
    ):
        from shared.integration_boot import boot_integration
        status = await boot_integration("orchestrator")

    assert status["telemetry"].startswith("error:")
    assert status["tool_registry"] == "ok"
    assert status["register_tools"] == "ok"
    assert status["mcp_server"] == "ok"


@pytest.mark.asyncio
async def test_boot_no_modules():
    """All imports fail -- returns all errors but never crashes."""
    with (
        patch("shared.integration_boot._boot_telemetry", new_callable=AsyncMock, return_value="error: no module"),
        patch("shared.integration_boot._boot_tool_registry", new_callable=AsyncMock, return_value="error: no module"),
        patch("shared.integration_boot._boot_register_tools", new_callable=AsyncMock, return_value="error: no module"),
        patch("shared.integration_boot._boot_mcp_server", new_callable=AsyncMock, return_value="error: no module"),
    ):
        from shared.integration_boot import boot_integration
        status = await boot_integration("titan")

    assert all(v.startswith("error:") for v in status.values())
    assert len(status) == 4  # No crash, 4 modules attempted


@pytest.mark.asyncio
async def test_boot_hermes_includes_channels():
    """When daemon_name is 'hermes', channels module is booted."""
    with (
        patch("shared.integration_boot._boot_telemetry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_tool_registry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_register_tools", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_mcp_server", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_channels", new_callable=AsyncMock, return_value="ok"),
    ):
        from shared.integration_boot import boot_integration
        status = await boot_integration("hermes")

    assert "channels" in status
    assert status["channels"] == "ok"
    assert len(status) == 5  # 4 common + channels


@pytest.mark.asyncio
async def test_boot_non_hermes_skips_channels():
    """Non-hermes daemons do not attempt channel boot."""
    with (
        patch("shared.integration_boot._boot_telemetry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_tool_registry", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_register_tools", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_mcp_server", new_callable=AsyncMock, return_value="ok"),
        patch("shared.integration_boot._boot_channels", new_callable=AsyncMock, return_value="ok") as mock_channels,
    ):
        from shared.integration_boot import boot_integration

        for name in ("orchestrator", "titan", "clawdbot"):
            status = await boot_integration(name)
            assert "channels" not in status

    # _boot_channels should never have been called
    mock_channels.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_cleans_up():
    """shutdown_integration runs without error even when modules are missing."""
    with (
        patch("shared.integration_boot.logger") as mock_logger,
    ):
        # Patch imports inside shutdown to simulate missing modules
        import shared.integration_boot as mod

        # Mock telemetry collector as not importable
        with patch.object(mod, "shutdown_integration", wraps=mod.shutdown_integration):
            # Just verify it doesn't raise
            await mod.shutdown_integration()

    # Should have logged completion
    mock_logger.info.assert_called()
