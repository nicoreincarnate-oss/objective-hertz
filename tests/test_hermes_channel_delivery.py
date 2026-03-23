from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _FakeChannel:
    channel_id = "slack"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def connect(self) -> None:
        return None

    def send(self, channel, content, *, conversation_id="", metadata=None) -> bool:
        self.sent.append(
            {
                "channel": channel,
                "content": content,
                "conversation_id": conversation_id,
                "metadata": metadata or {},
            }
        )
        return True


@pytest.mark.asyncio
async def test_send_operator_message_uses_configured_channel(monkeypatch):
    from hermes import alerts

    fake_channel = _FakeChannel()
    fake_config = SimpleNamespace(
        channel=SimpleNamespace(
            default_channel="slack",
            telegram=SimpleNamespace(allowed_chat_ids=""),
            email=SimpleNamespace(username=""),
        )
    )

    monkeypatch.setattr(
        alerts,
        "_get_channel_backend",
        lambda: (fake_channel, fake_config, "slack"),
    )

    fallback = AsyncMock(return_value=False)
    monkeypatch.setattr(alerts, "_send_telegram", fallback)

    result = await alerts.send_operator_message(
        "hello world",
        target="C123",
        conversation_id="thread-1",
        metadata={"kind": "test"},
    )

    assert result["sent"] is True
    assert result["channel"] == "slack"
    assert result["target"] == "C123"
    assert result["fallback"] is False
    assert fake_channel.sent == [
        {
            "channel": "C123",
            "content": "hello world",
            "conversation_id": "thread-1",
            "metadata": {"kind": "test"},
        }
    ]
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_alerts_does_not_ack_failed_delivery(monkeypatch):
    from hermes import alerts

    fetch_all = AsyncMock(return_value=[
        {
            "id": 7,
            "event_type": "urgent_alert",
            "payload": {"sender": "openjarvis", "message": "disk full"},
            "created_at": "2026-03-22T10:00:00Z",
        }
    ])
    execute = AsyncMock()
    send_operator_message = AsyncMock(return_value={"sent": False})

    monkeypatch.setattr(alerts, "fetch_all", fetch_all)
    monkeypatch.setattr(alerts, "execute", execute)
    monkeypatch.setattr(alerts, "send_operator_message", send_operator_message)

    await alerts.dispatch_alerts()

    send_operator_message.assert_awaited_once()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_send_uses_channel_layer(monkeypatch, patch_db):
    from hermes import a2a_server
    from hermes import alerts

    send_operator_message = AsyncMock(
        return_value={
            "sent": True,
            "channel": "slack",
            "target": "C999",
            "fallback": False,
        }
    )
    monkeypatch.setattr(alerts, "send_operator_message", send_operator_message)

    result = await a2a_server._message_send(
        text="hello operator",
        channel="C999",
        conversation_id="thread-9",
        metadata={"subject": "Status"},
    )

    assert result["sent"] is True
    assert result["channel"] == "slack"
    assert result["target"] == "C999"
    assert result["message"] == "hello operator"
    send_operator_message.assert_awaited_once_with(
        "*[Message]* hello operator",
        target="C999",
        conversation_id="thread-9",
        metadata={"subject": "Status"},
    )
