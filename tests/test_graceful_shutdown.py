"""Regression checks for graceful shutdown handling."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch


def load_agent_base():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.close_pool = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules.pop("shared.agent_base", None)
    module = importlib.import_module("shared.agent_base")
    return module, module.AgentBase


def run(coro):
    return asyncio.run(coro)


def test_agent_base_waits_for_work_to_drain():
    _, AgentBase = load_agent_base()

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

    async def scenario():
        agent.begin_work("task:1")

        async def finish_later():
            await asyncio.sleep(0.01)
            agent.finish_work("task:1")

        asyncio.create_task(finish_later())
        return await agent.wait_for_work_drain(timeout=0.2)

    assert run(scenario()) is True


def test_agent_base_can_requeue_stale_tasks_for_same_agent():
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

    with patch.object(module.db, "fetch_all", AsyncMock(return_value=[{"id": 1}, {"id": 2}])) as fetch_all:
        count = run(agent.requeue_stale_tasks())

    assert count == 2
    query, params = fetch_all.await_args.args
    assert "SET status = 'pending'" in query
    assert params == ("dummy",)


def test_titan_tracks_pipeline_stages_for_shutdown():
    code = open("/Users/majovega/Desktop/objective-hertz/titan/daemon.py").read()
    assert 'self.begin_work("loop:cycle")' in code
    assert 'work_id = f"stage:{stage_name}"' in code
    assert "await self.wait_for_work_drain()" in code


def test_clawdbot_requeues_stale_tasks_and_waits_for_drain():
    code = open("/Users/majovega/Desktop/objective-hertz/clawdbot/daemon.py").read()
    assert "await self.requeue_stale_tasks()" in code
    assert 'work_id = f"task:{task[\'id\']}"' in code
    assert "await self.wait_for_work_drain()" in code
