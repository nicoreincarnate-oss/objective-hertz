"""Regression tests for demo-before-proposal policy."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def load_close_deal_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.set_config = AsyncMock()
    fake_db.increment_config_int = AsyncMock(return_value=1)

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = object()

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules.pop("titan.pipeline.close_deal", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state_machine
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    return importlib.import_module("titan.pipeline.close_deal")


def test_build_demo_and_propose_stops_when_demo_build_fails():
    close_deal = load_close_deal_module()

    lead = {
        "id": 42,
        "business_name": "Acme Plumbing",
        "contact_name": "Nico",
        "email": "hello@example.com",
    }

    with patch.object(close_deal, "get_config", AsyncMock(side_effect=[0, 10])):
        with patch.object(close_deal, "_build_demo_site", AsyncMock(return_value="")):
            with patch.object(close_deal, "_generate_proposal", AsyncMock()) as generate:
                with patch.object(close_deal, "_send_proposal", AsyncMock()) as send:
                    with patch.object(close_deal, "emit_event", AsyncMock()) as emit_event:
                        run(close_deal._build_demo_and_propose(lead))

    generate.assert_not_awaited()
    send.assert_not_awaited()
    emit_event.assert_awaited()


def test_mark_sale_closed_uses_atomic_sales_increment():
    code = (ROOT / "titan" / "pipeline" / "close_deal.py").read_text()

    assert 'increment_config_int("sales_completed", 1, default=0)' in code
    assert 'await set_config("sales_completed", sales + 1)' not in code


def test_send_proposal_reuses_existing_campaign_name_when_config_missing():
    close_deal = load_close_deal_module()

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    class FakeInstantlyClient:
        def __init__(self):
            self.create_campaign = AsyncMock()
            self.activate_campaign = AsyncMock(return_value={})

        async def list_campaigns(self):
            return [{"id": "cmp_existing", "name": "perseus-proposals"}]

        async def close(self):
            return None

    lead = {
        "id": 42,
        "business_name": "Acme Plumbing",
        "contact_name": "Nico",
        "email": "hello@example.com",
    }
    proposal = {"subject": "Proposal", "body": "Body"}

    fake_tools = types.ModuleType("tools.instantly_client")
    client = FakeInstantlyClient()
    fake_tools.InstantlyClient = lambda: client

    with patch.dict(sys.modules, {"tools.instantly_client": fake_tools}):
        with patch.object(close_deal, "get_config", AsyncMock(return_value="")):
            with patch.object(close_deal, "set_config", AsyncMock()) as set_config:
                result = run(close_deal._send_proposal(lead, proposal))

    assert result is True
    client.create_campaign.assert_not_awaited()
    client.activate_campaign.assert_awaited_once_with("cmp_existing")
    set_config.assert_awaited_once_with("instantly_proposals_campaign_id", "cmp_existing")
    fake_compliance.send_to_instantly.assert_awaited_once()
