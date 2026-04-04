"""Tests for hermes.channel_manager — unified OJ channel wrapper."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake OJ channel objects
# ---------------------------------------------------------------------------


def _make_fake_status_enum():
    """Return a minimal ChannelStatus-like enum."""
    fake = types.SimpleNamespace(
        CONNECTED="connected",
        DISCONNECTED="disconnected",
        ERROR="error",
    )
    # Make the values behave like the real enum (.value returns itself)
    for attr in ("CONNECTED", "DISCONNECTED", "ERROR"):
        val = getattr(fake, attr)
        # Wrap in SimpleNamespace so `.value` works
        ns = types.SimpleNamespace(value=val)
        setattr(fake, attr, ns)
    return fake


_FAKE_STATUS = _make_fake_status_enum()


def _make_channel_instance(name: str, *, send_ok: bool = True, status_val=None):
    """Build a mock BaseChannel instance."""
    ch = MagicMock()
    ch.channel_id = name
    ch.connect.return_value = None
    ch.disconnect.return_value = None
    ch.send.return_value = send_ok
    ch.list_channels.return_value = [name]
    ch.status.return_value = status_val or _FAKE_STATUS.CONNECTED
    return ch


def _make_channel_class(name: str, **kwargs):
    """Return a callable that produces a channel instance (acts as the class)."""
    instance = _make_channel_instance(name, **kwargs)

    def _cls(*_a, **_kw):
        return instance

    _cls._instance = instance
    return _cls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _env_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")


@pytest.fixture()
def _env_email(monkeypatch):
    monkeypatch.setenv("EMAIL_USERNAME", "bot@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_TO", "operator@example.com")


@pytest.fixture()
def _env_slack(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0001")


@pytest.fixture()
def _env_whatsapp(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setenv("WHATSAPP_TO", "+15551234567")


# ---------------------------------------------------------------------------
# Stub modules so imports of openjarvis don't require the full package
# ---------------------------------------------------------------------------


def _patch_oj_imports(channel_classes: dict[str, MagicMock] | None = None):
    """Return a patch context that stubs openjarvis imports.

    ``channel_classes`` maps channel names to fake classes returned by
    ``ChannelRegistry.get(name)``.
    """
    channel_classes = channel_classes or {}

    fake_stubs = types.ModuleType("openjarvis.channels._stubs")
    fake_stubs.ChannelStatus = _FAKE_STATUS

    fake_registry_mod = types.ModuleType("openjarvis.core.registry")
    registry_mock = MagicMock()

    def _get(name):
        if name in channel_classes:
            return channel_classes[name]
        raise KeyError(name)

    registry_mock.get.side_effect = _get
    fake_registry_mod.ChannelRegistry = registry_mock

    return patch.dict(
        sys.modules,
        {
            "openjarvis": types.ModuleType("openjarvis"),
            "openjarvis.channels": types.ModuleType("openjarvis.channels"),
            "openjarvis.channels._stubs": fake_stubs,
            "openjarvis.core": types.ModuleType("openjarvis.core"),
            "openjarvis.core.registry": fake_registry_mod,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_with_telegram_creds(_env_telegram):
    """ChannelManager should register telegram when creds are present."""
    tg_cls = _make_channel_class("telegram")

    with _patch_oj_imports({"telegram": tg_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        assert "telegram" in mgr.available_channels()
        tg_cls._instance.connect.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_no_creds(monkeypatch):
    """With no credentials set, channel list should be empty."""
    # Ensure none of the required env vars are set
    for var in ("TELEGRAM_BOT_TOKEN", "EMAIL_USERNAME", "EMAIL_PASSWORD",
                "SLACK_BOT_TOKEN", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        monkeypatch.delenv(var, raising=False)

    with _patch_oj_imports({}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        assert mgr.available_channels() == []


@pytest.mark.asyncio
async def test_send_telegram_message(_env_telegram):
    """send() should delegate to the OJ channel's send method."""
    tg_cls = _make_channel_class("telegram", send_ok=True)

    with _patch_oj_imports({"telegram": tg_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        ok = await mgr.send("telegram", "12345", "Hello operator")
        assert ok is True
        tg_cls._instance.send.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_message(_env_email):
    """send() should work for the email channel."""
    email_cls = _make_channel_class("email", send_ok=True)

    with _patch_oj_imports({"email": email_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        ok = await mgr.send(
            "email",
            "operator@example.com",
            "Daily report attached",
            metadata={"subject": "Perseus Daily Report"},
        )
        assert ok is True
        email_cls._instance.send.assert_called_once()


@pytest.mark.asyncio
async def test_send_alert_routing_critical(_env_telegram, _env_email):
    """Critical alerts should try all configured channels."""
    tg_cls = _make_channel_class("telegram", send_ok=True)
    email_cls = _make_channel_class("email", send_ok=True)

    with _patch_oj_imports({"telegram": tg_cls, "email": email_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        ok = await mgr.send_alert("CRITICAL: pipeline down", severity="critical")
        assert ok is True
        # Both channels should have been called
        tg_cls._instance.send.assert_called_once()
        email_cls._instance.send.assert_called_once()


@pytest.mark.asyncio
async def test_send_alert_routing_info(_env_telegram, _env_email):
    """Info alerts should only use telegram."""
    tg_cls = _make_channel_class("telegram", send_ok=True)
    email_cls = _make_channel_class("email", send_ok=True)

    with _patch_oj_imports({"telegram": tg_cls, "email": email_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        ok = await mgr.send_alert("FYI: 5 new leads", severity="info")
        assert ok is True
        tg_cls._instance.send.assert_called_once()
        email_cls._instance.send.assert_not_called()


@pytest.mark.asyncio
async def test_available_channels(_env_telegram, _env_slack):
    """available_channels should list all initialized channels."""
    tg_cls = _make_channel_class("telegram")
    slack_cls = _make_channel_class("slack")

    with _patch_oj_imports({"telegram": tg_cls, "slack": slack_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        channels = mgr.available_channels()
        assert "telegram" in channels
        assert "slack" in channels


@pytest.mark.asyncio
async def test_oj_unavailable_graceful(monkeypatch):
    """When OJ modules can't be imported, manager should degrade gracefully."""
    # Remove any openjarvis from sys.modules so import fails
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # Patch so that importing openjarvis.channels._stubs raises ImportError
    with patch.dict(sys.modules, {
        "openjarvis": None,
        "openjarvis.channels": None,
        "openjarvis.channels._stubs": None,
        "openjarvis.core": None,
        "openjarvis.core.registry": None,
    }):
        # Need to reload the module with broken imports
        from hermes import channel_manager as cm_mod

        mgr = cm_mod.ChannelManager()
        await mgr.initialize()

        assert mgr.available_channels() == []
        assert mgr._oj_available is False
        assert mgr._initialized is True


@pytest.mark.asyncio
async def test_shutdown_disconnects_all(_env_telegram, _env_email):
    """shutdown() should call disconnect on every channel and clear state."""
    tg_cls = _make_channel_class("telegram")
    email_cls = _make_channel_class("email")

    with _patch_oj_imports({"telegram": tg_cls, "email": email_cls}):
        from hermes.channel_manager import ChannelManager

        mgr = ChannelManager()
        await mgr.initialize()

        assert len(mgr.available_channels()) == 2

        await mgr.shutdown()

        tg_cls._instance.disconnect.assert_called_once()
        email_cls._instance.disconnect.assert_called_once()
        assert mgr.available_channels() == []
        assert mgr._initialized is False
