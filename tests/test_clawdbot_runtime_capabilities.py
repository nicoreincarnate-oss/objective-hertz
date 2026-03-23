from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_clawdbot_bootstrap_activates_runtime_capabilities(monkeypatch, patch_db):
    from clawdbot.daemon import ClawdBotDaemon

    daemon = ClawdBotDaemon()

    monkeypatch.setattr(
        "clawdbot.daemon._agent_mesh_entries",
        lambda: [{"id": "mesh-1", "name": "clawdbot-agent-orchestrator", "agent_type": "orchestrator", "status": "idle"}],
    )
    monkeypatch.setattr("clawdbot.daemon._has_android_runtime", lambda: False)
    monkeypatch.setattr("clawdbot.daemon._whatsapp_channel_enabled", lambda: False)
    monkeypatch.setattr("clawdbot.daemon._has_telnyx_voice_config", lambda: False)
    monkeypatch.setattr("clawdbot.daemon._has_twilio_voice_config", lambda: False)
    monkeypatch.setattr("clawdbot.daemon._env_enabled", lambda _name: False)

    resolved = AsyncMock(return_value={"resolved": True, "method": "runtime_bootstrap"})
    monkeypatch.setattr("clawdbot.capability_resolver.resolve_capability", resolved)
    monkeypatch.setattr("clawdbot.capability_resolver.is_already_resolved", AsyncMock(return_value=False))

    await daemon._bootstrap_runtime_capabilities(force=True)

    requested = [call.args[0] for call in resolved.await_args_list]
    assert requested == ["browser", "scraper", "web_search"]

    runtime = patch_db.tables["system_config"]["clawdbot_capability_runtime"]
    assert runtime["browser"]["resolved"] is True
    assert runtime["agent_orchestrator"]["resolved"] is True
    assert patch_db.tables["system_config"]["clawdbot_agent_mesh"][0]["name"] == "clawdbot-agent-orchestrator"


@pytest.mark.asyncio
async def test_handle_agent_orchestration_creates_mesh_tasks(monkeypatch, patch_db):
    from clawdbot import daemon as clawdbot_daemon

    class _FakeManager:
        def create_task(self, agent_id: str, description: str, status: str = "pending"):
            return {
                "id": f"task-{agent_id}",
                "agent_id": agent_id,
                "description": description,
                "status": status,
            }

    monkeypatch.setattr(
        "clawdbot.daemon._agent_mesh_entries",
        lambda: [
            {"id": "orch-1", "name": "clawdbot-agent-orchestrator", "agent_type": "orchestrator", "status": "idle"},
            {"id": "worker-1", "name": "clawdbot-agent-network", "agent_type": "react", "status": "idle"},
        ],
    )
    monkeypatch.setattr("shared.oj_bridge.get_agent_manager", lambda: _FakeManager())

    result = await clawdbot_daemon.handle_agent_orchestration({"objective": "audit prospect research"})

    assert result["status"] == "delegated"
    assert len(result["tasks"]) == 2
    assert patch_db.tables["events"][-1]["event_type"] == "agent_mesh_task_created"


@pytest.mark.asyncio
async def test_handle_android_automation_tap_runs_adb(monkeypatch, patch_db):
    from clawdbot import daemon as clawdbot_daemon

    monkeypatch.setattr("clawdbot.daemon._has_android_runtime", lambda: True)
    monkeypatch.setattr("clawdbot.capability_resolver.is_already_resolved", AsyncMock(return_value=True))
    monkeypatch.setattr("clawdbot.capability_resolver.resolve_capability", AsyncMock())

    run_adb = AsyncMock(return_value=(b"", ""))
    monkeypatch.setattr("clawdbot.daemon._run_adb_command", run_adb)

    result = await clawdbot_daemon.handle_android_automation(
        {"action": "tap", "serial": "emulator-5554", "x": 120, "y": 480}
    )

    assert result["status"] == "ok"
    run_adb.assert_awaited_once_with(
        ["shell", "input", "tap", "120", "480"],
        serial="emulator-5554",
    )
    assert patch_db.tables["events"][-1]["event_type"] == "android_automation_result"


@pytest.mark.asyncio
async def test_handle_voice_call_requests_signup_when_unconfigured(monkeypatch):
    from clawdbot import daemon as clawdbot_daemon

    signup = AsyncMock(return_value={"status": "awaiting_operator", "service": "telnyx_voice"})
    monkeypatch.setattr("clawdbot.daemon._resolve_voice_provider", lambda _requested="": "")
    monkeypatch.setattr("clawdbot.daemon.handle_service_signup", signup)

    result = await clawdbot_daemon.handle_voice_call({"to": "+15555550123"})

    assert result["status"] == "awaiting_operator"
    signup.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_whatsapp_message_uses_channel_layer(monkeypatch, patch_db):
    from clawdbot import daemon as clawdbot_daemon

    class _FakeChannel:
        def __init__(self) -> None:
            self.sent = []

        def send(self, target, content, *, conversation_id="", metadata=None):
            self.sent.append(
                {
                    "target": target,
                    "content": content,
                    "conversation_id": conversation_id,
                    "metadata": metadata or {},
                }
            )
            return True

    fake_channel = _FakeChannel()

    monkeypatch.setattr("clawdbot.capability_resolver.is_already_resolved", AsyncMock(return_value=True))
    monkeypatch.setattr("clawdbot.capability_resolver.resolve_capability", AsyncMock())
    monkeypatch.setattr("clawdbot.daemon._get_channel_backend", lambda *_args, **_kwargs: (fake_channel, None, "whatsapp"))
    monkeypatch.setattr("clawdbot.daemon._resolve_whatsapp_target", lambda *_args, **_kwargs: "15551234567")

    result = await clawdbot_daemon.handle_whatsapp_message(
        {"message": "hello", "conversation_id": "thread-7"}
    )

    assert result["sent"] is True
    assert result["channel"] == "whatsapp"
    assert fake_channel.sent == [
        {
            "target": "15551234567",
            "content": "hello",
            "conversation_id": "thread-7",
            "metadata": {},
        }
    ]
    assert patch_db.tables["events"][-1]["event_type"] == "whatsapp_message_sent"
