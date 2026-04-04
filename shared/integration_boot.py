"""Perseus Full Integration boot sequence.

Initializes all integration modules in the correct order at daemon startup.
Called from orchestrator.py and individual daemon entry points.

Boot order:
1. Telemetry (first -- so everything after is tracked)
2. ToolRegistry auto-scan (discover all tools)
3. Register Perseus-specific tools
4. MCP server (expose tools via MCP)
5. Channel manager (Hermes only)

Each step is independent and gracefully degradable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def boot_integration(daemon_name: str = "orchestrator") -> dict:
    """
    Initialize all integration modules.

    Args:
        daemon_name: Which daemon is booting (orchestrator, titan, hermes, clawdbot)

    Returns:
        Status dict: {module_name: "ok" | "skipped" | "error: reason"}
    """
    status: dict[str, str] = {}

    # 1. Telemetry
    status["telemetry"] = await _boot_telemetry()

    # 2. Tool Registry
    status["tool_registry"] = await _boot_tool_registry()

    # 3. Register tools
    status["register_tools"] = await _boot_register_tools()

    # 4. MCP Server
    status["mcp_server"] = await _boot_mcp_server()

    # 5. Channel Manager (Hermes only)
    if daemon_name == "hermes":
        status["channels"] = await _boot_channels()

    # Log summary
    ok = sum(1 for v in status.values() if v == "ok")
    total = len(status)
    logger.info(
        "Integration boot: %d/%d modules initialized for %s", ok, total, daemon_name
    )

    return status


async def shutdown_integration() -> None:
    """Clean shutdown of all integration modules."""
    # Telemetry flush
    try:
        from shared.telemetry import collector

        if collector._initialized:
            logger.info("Telemetry collector shutdown")
    except Exception as exc:
        logger.debug("Telemetry shutdown skipped: %s", exc)

    # MCP server stop
    try:
        from shared.mcp_server import stop_mcp_server

        await stop_mcp_server()
        logger.info("MCP server stopped")
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("MCP server shutdown skipped: %s", exc)

    logger.info("Integration shutdown complete")


# ---------------------------------------------------------------------------
# Private boot helpers -- each returns "ok", "skipped", or "error: reason"
# ---------------------------------------------------------------------------


async def _boot_telemetry() -> str:
    """Initialize the global TelemetryCollector."""
    try:
        from shared.telemetry import collector

        await collector.initialize()
        logger.info("Telemetry collector initialized")
        return "ok"
    except ImportError:
        logger.debug("shared.telemetry not available -- skipping")
        return "skipped"
    except Exception as exc:
        logger.warning("Telemetry init failed: %s", exc)
        return f"error: {exc}"


async def _boot_tool_registry() -> str:
    """Run auto_scan_all to discover all available tools."""
    try:
        from shared.tool_registry import auto_scan_all

        count = auto_scan_all()
        logger.info("Tool registry: scanned %s tools", count)
        return "ok"
    except ImportError:
        logger.debug("shared.tool_registry not available -- skipping")
        return "skipped"
    except Exception as exc:
        logger.warning("Tool registry scan failed: %s", exc)
        return f"error: {exc}"


async def _boot_register_tools() -> str:
    """Register Perseus-specific tools into the registry."""
    try:
        from shared.register_tools import register_all_tools

        count = await register_all_tools()
        logger.info("Registered %s Perseus tools", count)
        return "ok"
    except ImportError:
        logger.debug("shared.register_tools not available -- skipping")
        return "skipped"
    except Exception as exc:
        logger.warning("Tool registration failed: %s", exc)
        return f"error: {exc}"


async def _boot_mcp_server() -> str:
    """Start the MCP server (already wired into oj_bridge, but ensure it's up)."""
    try:
        from shared.mcp_server import start_mcp_server

        ok = await start_mcp_server()
        if ok:
            logger.info("MCP server started")
            return "ok"
        else:
            logger.debug("MCP server start returned False -- skipped")
            return "skipped"
    except ImportError:
        logger.debug("shared.mcp_server not available -- skipping")
        return "skipped"
    except Exception as exc:
        logger.warning("MCP server start failed: %s", exc)
        return f"error: {exc}"


async def _boot_channels() -> str:
    """Initialize the Hermes ChannelManager."""
    try:
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()
        logger.info("ChannelManager initialized")
        return "ok"
    except ImportError:
        logger.debug("hermes.channel_manager not available -- skipping")
        return "skipped"
    except Exception as exc:
        logger.warning("ChannelManager init failed: %s", exc)
        return f"error: {exc}"
