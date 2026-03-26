"""Behavioral tests for ClawdBot activation in the pipeline.

Tests that:
1. All ClawdBot task types are routable via TASK_ROUTING
2. ClawdBot capability handlers exist on the A2A server
3. Pipeline modules import and call ClawdBot-routed comms functions
"""

from unittest.mock import AsyncMock, patch

import pytest

from shared.task_routing import TASK_ROUTING


class TestClawdBotRouting:
    """All ClawdBot tasks must be routable through A2A dispatch."""

    EXPECTED_CLAWDBOT_TASKS = [
        "web_scrape",
        "skill_execute",
        "enrich_lead",
        "enrich_leads",
        "browser_task",
        "site_verify",
        "verify_single_site",
        "site_verify_batch",
        "verify_demo_site",
    ]

    @pytest.mark.parametrize("task_type", EXPECTED_CLAWDBOT_TASKS)
    def test_task_routes_to_clawdbot(self, task_type):
        assert TASK_ROUTING.get(task_type) == "clawdbot", \
            f"{task_type} should route to clawdbot, got {TASK_ROUTING.get(task_type)}"


class TestDeploySiteDispatchesVerification:
    """deploy_site._verify_deployment must dispatch verify_single_site to ClawdBot."""

    @pytest.mark.asyncio
    async def test_verify_deployment_dispatches_correct_task_type(self):
        """_verify_deployment must call request_task_result('verify_single_site', ...)."""
        mock_rtr = AsyncMock(return_value={"is_live": True, "status_code": 200})
        with patch("titan.pipeline.deploy_site.request_task_result", mock_rtr):
            from titan.pipeline.deploy_site import _verify_deployment
            result = await _verify_deployment("https://testco.netlify.app")

        mock_rtr.assert_awaited_once()
        call_args = mock_rtr.call_args
        assert call_args[0][0] == "verify_single_site"
        assert call_args[1]["payload"]["url"] == "https://testco.netlify.app"
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_deployment_returns_false_on_timeout(self):
        """When ClawdBot is unreachable, returns False (not a silent success)."""
        mock_rtr = AsyncMock(return_value=None)
        with patch("titan.pipeline.deploy_site.request_task_result", mock_rtr):
            from titan.pipeline.deploy_site import _verify_deployment
            result = await _verify_deployment("https://testco.netlify.app")

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_deployment_returns_false_on_empty_url(self):
        from titan.pipeline.deploy_site import _verify_deployment
        result = await _verify_deployment("")
        assert result is False


def _firecrawl_stub():
    """Plain module stub for tools.firecrawl_client — no AsyncMock, no coroutine leaks."""
    import types
    mod = types.ModuleType("tools.firecrawl_client")
    mod.scrape_url = lambda url: {}
    mod.enrich_business_profile = lambda **kw: {}
    return mod


class TestLeadResearchDispatchesScrape:
    """lead_research._scrape_business_info must dispatch all 3 ClawdBot tasks."""

    @pytest.mark.asyncio
    async def test_dispatches_all_three_tasks_with_correct_payloads(self):
        """_scrape_business_info dispatches web_scrape, browser_task, and enrich_lead."""
        captured_calls = []

        async def mock_rtr(task_type, payload=None, timeout_seconds=60):
            captured_calls.append((task_type, payload, timeout_seconds))
            return {"ok": False}  # Return non-ok so fallbacks don't complicate

        with patch("titan.pipeline.lead_research.request_task_result", mock_rtr):
            with patch.dict("sys.modules", {"tools.firecrawl_client": _firecrawl_stub()}):
                from titan.pipeline.lead_research import _scrape_business_info
                lead = {
                    "id": 7, "business_name": "TestCo",
                    "website_url": "https://testco.com", "industry": "tech",
                }
                await _scrape_business_info(lead)

        calls_by_type = {c[0]: c for c in captured_calls}

        # web_scrape: dispatched with the lead's URL
        assert "web_scrape" in calls_by_type, f"Missing web_scrape, got: {list(calls_by_type)}"
        assert calls_by_type["web_scrape"][1] == {"url": "https://testco.com"}

        # browser_task: dispatched with the lead's URL and a description
        assert "browser_task" in calls_by_type, f"Missing browser_task, got: {list(calls_by_type)}"
        bt_payload = calls_by_type["browser_task"][1]
        assert bt_payload["url"] == "https://testco.com"
        assert "TestCo" in bt_payload["description"]

        # enrich_lead: always dispatched with the client_id
        assert "enrich_lead" in calls_by_type, f"Missing enrich_lead, got: {list(calls_by_type)}"
        assert calls_by_type["enrich_lead"][1] == {"client_id": 7}

    @pytest.mark.asyncio
    async def test_enrich_dispatched_even_without_url(self):
        """enrich_lead is always dispatched, even when lead has no website_url."""
        captured_calls = []

        async def mock_rtr(task_type, payload=None, timeout_seconds=60):
            captured_calls.append((task_type, payload))
            return {"ok": False}

        with patch("titan.pipeline.lead_research.request_task_result", mock_rtr):
            with patch.dict("sys.modules", {"tools.firecrawl_client": _firecrawl_stub()}):
                from titan.pipeline.lead_research import _scrape_business_info
                lead = {
                    "id": 8, "business_name": "NoUrlCo",
                    "website_url": "", "industry": "food",
                }
                await _scrape_business_info(lead)

        task_types = [c[0] for c in captured_calls]
        # web_scrape and browser_task should NOT be dispatched (no URL)
        assert "web_scrape" not in task_types
        assert "browser_task" not in task_types
        # enrich_lead should always fire
        assert "enrich_lead" in task_types
        enrich_call = [c for c in captured_calls if c[0] == "enrich_lead"][0]
        assert enrich_call[1] == {"client_id": 8}


class TestClawdBotDispatchPath:
    """The shared.comms dispatch path must be functional for ClawdBot tasks."""

    @pytest.mark.asyncio
    async def test_request_task_inserts_to_db_when_a2a_off(self):
        """request_task for a ClawdBot task must insert into DB task queue."""
        from shared.comms import request_task

        with patch("shared.comms.db") as mock_db:
            mock_db.insert_task = AsyncMock(return_value=123)
            with patch("shared.comms._USE_A2A", False):
                task_id = await request_task(
                    "verify_single_site",
                    {"url": "https://example.com"},
                )

        mock_db.insert_task.assert_awaited_once()
        call_args = mock_db.insert_task.call_args
        assert call_args[0][0] == "verify_single_site"
        assert task_id == 123
