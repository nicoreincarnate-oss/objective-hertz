"""Regression tests for full-site QA before deployment."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


def load_build_site_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    sys.modules.pop("titan.pipeline.build_site", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state_machine
    return importlib.import_module("titan.pipeline.build_site")


def test_build_sites_blocks_deployment_when_full_site_qa_fails():
    build_site = load_build_site_module()

    lead = {
        "id": 9,
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "research_summary": "",
        "research_facts": "",
        "language": "en",
        "country": "Mexico",
        "city": "Mazatlan",
        "demo_site_url": "https://demo.example",
    }

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.request_task_result = AsyncMock(
        return_value={"ok": True, "result": {"passed": False, "reason": "no_placeholder_assets"}}
    )
    # Save any real module so we can restore after test
    _saved_comms = sys.modules.get("shared.comms")
    sys.modules["shared.comms"] = fake_comms

    try:
        with patch.object(build_site, "fetch_all", AsyncMock(return_value=[lead])):
            with patch.object(build_site, "_build_full_site", AsyncMock(return_value="https://final.example")):
                with patch.object(build_site, "emit_event", AsyncMock()) as emit_event:
                    run(build_site.build_sites())

        build_site.execute.assert_not_awaited()
        blocked_calls = [call for call in emit_event.await_args_list if call.args and call.args[0] == "site_deploy_blocked"]
        assert blocked_calls
    finally:
        # Restore sys.modules to prevent contaminating subsequent test files
        if _saved_comms is not None:
            sys.modules["shared.comms"] = _saved_comms
        else:
            sys.modules.pop("shared.comms", None)
