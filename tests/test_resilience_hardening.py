"""Regression tests for deliverability and operational hardening."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def run(coro):
    return asyncio.run(coro)


def load_deliverability_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.fetch_val = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.set_config = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules.pop("titan.deliverability", None)
    return importlib.import_module("titan.deliverability")


def load_email_send_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.set_config = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.transaction = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.email_send", None)
    return importlib.import_module("titan.pipeline.email_send")


def load_agent_base():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.close_pool = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules.pop("shared.agent_base", None)
    module = importlib.import_module("shared.agent_base")
    return module, module.AgentBase


def test_warmup_day_only_advances_after_hitting_80_percent_of_ramp_target():
    module = load_deliverability_module()

    config = {
        "warm_up_phase": True,
        "warmup_day": 10,
        "email_daily_target": 90,
    }
    written = {}

    async def fake_get_config(key, default=None):
        return config.get(key, default)

    async def fake_set_config(key, value):
        written[key] = value

    with patch.object(module, "get_config", AsyncMock(side_effect=fake_get_config)):
        with patch.object(module, "set_config", AsyncMock(side_effect=fake_set_config)):
            with patch.object(module, "fetch_val", AsyncMock(return_value=71)):
                run(module._enforce_warmup_volume())

    assert "warmup_day" not in written


def test_provider_bucketing_caps_gmail_and_outlook_before_other_domains():
    module = load_email_send_module()
    leads = [
        {"email": "one@gmail.com"},
        {"email": "two@googlemail.com"},
        {"email": "three@gmail.com"},
        {"email": "four@outlook.com"},
        {"email": "five@hotmail.com"},
        {"email": "six@yahoo.com"},
    ]

    limited = module._apply_provider_bucketing(
        leads,
        batch_size=6,
        sent_today_by_provider={"gmail": 98, "outlook": 99, "other": 49},
    )

    assert [lead["email"] for lead in limited] == [
        "one@gmail.com",
        "two@googlemail.com",
        "four@outlook.com",
        "six@yahoo.com",
    ]


def test_sync_campaign_analytics_updates_sequence_engagement_timestamps():
    module = load_email_send_module()

    fake_client = types.SimpleNamespace(
        get_campaign_analytics=AsyncMock(return_value={"domain": "perseus", "sent": 12, "opens": 4, "replies": 1}),
        list_leads=AsyncMock(return_value=[
            {"email": "opened@example.com", "email_open_count": 2, "email_reply_count": 0},
            {"email": "replied@example.com", "email_open_count": 1, "email_reply_count": 1},
        ]),
        close=AsyncMock(),
    )

    fake_tools = types.ModuleType("tools.instantly_client")
    fake_tools.InstantlyClient = lambda: fake_client
    sys.modules["tools.instantly_client"] = fake_tools

    with patch.object(module, "get_config", AsyncMock(return_value="cmp_123")):
        with patch.object(module, "execute", AsyncMock()) as mocked_execute:
            run(module.sync_campaign_analytics())

    queries = [call.args[0] for call in mocked_execute.await_args_list]
    assert any("INSERT INTO outreach_metrics" in query for query in queries)
    assert any("SET opened_at = COALESCE(opened_at, NOW())" in query for query in queries)
    assert any("replied_at = COALESCE(replied_at, NOW())" in query for query in queries)


def test_agent_base_dead_letters_task_after_third_failure():
    module, AgentBase = load_agent_base()

    class DummyAgent(AgentBase):
        name = "dummy"
        description = "dummy"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def health_check(self) -> dict:
            return {"status": "ok"}

    agent = DummyAgent()

    with patch.object(module.db, "fetch_one", AsyncMock(return_value={"status": "dead_letter", "retry_count": 3})) as mocked_fetch_one:
        with patch.object(module.db, "emit_event", AsyncMock()) as mocked_emit:
            run(agent.fail_task(17, "still failing"))

    query, params = mocked_fetch_one.await_args.args
    assert "retry_count = COALESCE(retry_count, 0) + 1" in query
    assert "THEN 'dead_letter'" in query
    assert params == ("still failing", "dummy", 17)
    mocked_emit.assert_awaited_once()


def test_agent_base_retries_failed_tasks_before_dead_lettering():
    module, AgentBase = load_agent_base()

    class DummyAgent(AgentBase):
        name = "dummy"
        description = "dummy"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def health_check(self) -> dict:
            return {"status": "ok"}

    agent = DummyAgent()

    with patch.object(module.db, "fetch_all", AsyncMock(return_value=[{"id": 3}, {"id": 4}])) as mocked_fetch_all:
        tasks = run(agent.get_pending_tasks())

    assert tasks == [{"id": 3}, {"id": 4}]
    query = mocked_fetch_all.await_args.args[0]
    assert "status = 'pending'" in query
    assert "status = 'failed' AND COALESCE(retry_count, 0) < 3" in query


def test_backup_and_restore_scripts_are_wired_into_makefile():
    makefile = (ROOT / "Makefile").read_text()

    assert "backup:" in makefile
    assert "restore:" in makefile
    assert (ROOT / "scripts" / "backup-postgres.sh").exists()
    assert (ROOT / "scripts" / "restore-from-backup.sh").exists()


def test_health_checks_include_disk_capacity_monitoring():
    code = (ROOT / "perseus" / "health.py").read_text()

    assert "async def _check_disk_space()" in code
    assert '"disk": disk_health' in code
    assert '"percent_used"' in code


def test_start_and_stop_scripts_wait_for_services_and_graceful_shutdown():
    start_code = (ROOT / "scripts" / "start-perseus.sh").read_text()
    stop_code = (ROOT / "scripts" / "stop-perseus.sh").read_text()

    assert "wait_for_pid()" in start_code
    assert "wait_for_http()" in start_code
    assert "http://localhost:8500/api/health" in start_code
    assert "SIGTERM_WAIT_SECONDS=30" in stop_code
    assert "kill -KILL" in stop_code


def test_launchagents_define_restart_throttle_and_backup_schedule():
    for plist_name in (
        "com.perseus.master.plist",
        "com.perseus.titan.plist",
        "com.perseus.clawdbot.plist",
        "com.perseus.dashboard.plist",
        "com.perseus.frontend.plist",
    ):
        plist = (ROOT / "scripts" / "launchagents" / plist_name).read_text()
        assert "<key>ThrottleInterval</key>" in plist
        assert "<integer>10</integer>" in plist

    backup_plist = (ROOT / "scripts" / "launchagents" / "com.perseus.backup.plist")
    assert backup_plist.exists()
    assert "<key>StartCalendarInterval</key>" in backup_plist.read_text()
