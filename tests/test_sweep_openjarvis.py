"""Completion sweep tests — 16 modules.

openjarvis_rust is NOT available; any call to get_rust_module() returns None.
All external I/O (httpx, DB, subprocess) is mocked.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Global: stub openjarvis_rust so it can never be imported
# ---------------------------------------------------------------------------
sys.modules["openjarvis_rust"] = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Helper: create a fake httpx response
# ---------------------------------------------------------------------------


def _fake_response(json_body: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# 1. openjarvis/a2a/client.py — A2AClient
# ===========================================================================

class TestA2AClient:
    def test_send_task_returns_task(self):
        from openjarvis.a2a.client import A2AClient

        result_payload = {
            "result": {"id": "t1", "state": "completed", "output": "hello"}
        }
        with patch("httpx.post", return_value=_fake_response(result_payload)):
            client = A2AClient("http://localhost:9000")
            task = client.send_task("do something")

        assert task.task_id == "t1"
        assert task.state == "completed"
        assert task.output_text == "hello"

    def test_get_task(self):
        from openjarvis.a2a.client import A2AClient

        result_payload = {"result": {"id": "t2", "state": "working", "output": ""}}
        with patch("httpx.post", return_value=_fake_response(result_payload)):
            client = A2AClient("http://localhost:9000")
            task = client.get_task("t2")

        assert task.task_id == "t2"
        assert task.state == "working"

    def test_cancel_task(self):
        from openjarvis.a2a.client import A2AClient

        result_payload = {"result": {"id": "t3", "state": "canceled"}}
        with patch("httpx.post", return_value=_fake_response(result_payload)):
            client = A2AClient("http://localhost:9000")
            task = client.cancel_task("t3")

        assert task.task_id == "t3"
        assert task.state == "canceled"

    def test_discover(self):
        from openjarvis.a2a.client import A2AClient

        card_data = {
            "name": "Titan",
            "description": "Revenue engine",
            "url": "http://localhost:9001",
            "version": "0.1.0",
            "capabilities": ["pipeline_status"],
            "skills": [],
        }
        with patch("httpx.get", return_value=_fake_response(card_data)):
            client = A2AClient("http://localhost:9001")
            card = client.discover()

        assert card.name == "Titan"
        assert "pipeline_status" in card.capabilities

    def test_send_task_per_call_timeout(self):
        """timeout kwarg is forwarded to httpx.post."""
        from openjarvis.a2a.client import A2AClient

        result_payload = {"result": {"id": "t4", "state": "completed", "output": "ok"}}
        with patch("httpx.post", return_value=_fake_response(result_payload)) as mock_post:
            client = A2AClient("http://localhost:9000", timeout=5.0)
            client.send_task("ping", timeout=99.0)

        _call_kwargs = mock_post.call_args
        assert _call_kwargs.kwargs.get("timeout") == 99.0


# ===========================================================================
# 2. openjarvis/a2a/tool.py — A2AAgentTool
# ===========================================================================

class TestA2AAgentTool:
    def _make_client(self, output: str = "result", state: str = "completed"):
        from openjarvis.a2a.client import A2AClient
        from openjarvis.a2a.protocol import A2ATask, TaskState

        client = MagicMock(spec=A2AClient)
        client.discover.side_effect = Exception("skip discover")
        task = A2ATask(task_id="x1", state=TaskState(state), output_text=output)
        client.send_task.return_value = task
        return client

    def test_spec_has_input_parameter(self):
        from openjarvis.a2a.tool import A2AAgentTool

        client = self._make_client()
        tool = A2AAgentTool(client, name="my_tool")

        spec = tool.spec
        assert spec.name == "my_tool"
        assert "input" in spec.parameters.get("properties", {})
        assert spec.category == "a2a"

    def test_execute_success(self):
        from openjarvis.a2a.tool import A2AAgentTool

        client = self._make_client(output="great output", state="completed")
        tool = A2AAgentTool(client, name="agent")

        result = tool.execute(input="do the thing")
        assert result.success is True
        assert result.content == "great output"

    def test_execute_no_input_fails(self):
        from openjarvis.a2a.tool import A2AAgentTool

        client = self._make_client()
        tool = A2AAgentTool(client, name="agent")

        result = tool.execute()
        assert result.success is False
        assert "No input" in result.content

    def test_execute_client_error_returns_failure(self):
        from openjarvis.a2a.client import A2AClient
        from openjarvis.a2a.tool import A2AAgentTool

        client = MagicMock(spec=A2AClient)
        client.discover.side_effect = Exception("no card")
        client.send_task.side_effect = RuntimeError("connection refused")
        tool = A2AAgentTool(client, name="broken")

        result = tool.execute(input="hello")
        assert result.success is False
        assert "connection refused" in result.content


# ===========================================================================
# 3. openjarvis/core/credentials.py
# ===========================================================================

class TestCredentials:
    def test_save_and_load_credential(self, tmp_path):
        from openjarvis.core.credentials import load_credentials, save_credential

        cred_file = tmp_path / "credentials.toml"
        save_credential("slack", "SLACK_BOT_TOKEN", "xoxb-test", path=cred_file)

        creds = load_credentials(path=cred_file)
        assert creds["slack"]["SLACK_BOT_TOKEN"] == "xoxb-test"

    def test_save_sets_env(self, tmp_path):
        from openjarvis.core.credentials import save_credential

        cred_file = tmp_path / "credentials.toml"
        save_credential("telegram", "TELEGRAM_BOT_TOKEN", "bot123", path=cred_file)
        assert os.environ.get("TELEGRAM_BOT_TOKEN") == "bot123"

    def test_unknown_key_raises(self, tmp_path):
        from openjarvis.core.credentials import save_credential

        with pytest.raises(ValueError, match="Unknown credential key"):
            save_credential("slack", "NONEXISTENT_KEY", "val",
                            path=tmp_path / "c.toml")

    def test_empty_value_raises(self, tmp_path):
        from openjarvis.core.credentials import save_credential

        with pytest.raises(ValueError, match="not be empty"):
            save_credential("slack", "SLACK_BOT_TOKEN", "   ",
                            path=tmp_path / "c.toml")

    def test_inject_credentials(self, tmp_path):
        from openjarvis.core.credentials import inject_credentials, save_credential

        cred_file = tmp_path / "credentials.toml"
        save_credential("discord", "DISCORD_BOT_TOKEN", "disc999", path=cred_file)
        # Remove from env to test injection
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        inject_credentials(path=cred_file)
        assert os.environ.get("DISCORD_BOT_TOKEN") == "disc999"

    def test_load_missing_file_returns_empty(self, tmp_path):
        from openjarvis.core.credentials import load_credentials

        result = load_credentials(path=tmp_path / "does_not_exist.toml")
        assert result == {}


# ===========================================================================
# 4. openjarvis/core/decisions.py — DecisionAudit
# ===========================================================================

class TestDecisionAudit:
    @pytest.fixture
    def audit(self, tmp_path):
        from openjarvis.core.decisions import DecisionAudit

        return DecisionAudit(db_path=str(tmp_path / "decisions.db"))

    def test_record_and_retrieve(self, audit):
        did = audit.record_decision(
            agent="titan",
            decision_type="route",
            context={"input": "email"},
            decision={"route": "stage_3"},
            reasoning="best match",
        )
        assert did > 0
        row = audit.get_by_id(did)
        assert row["agent"] == "titan"
        assert row["decision_type"] == "route"
        assert row["reasoning"] == "best match"
        assert row["outcome"] is None

    def test_record_outcome(self, audit):
        did = audit.record_decision("agent", "task", {}, {})
        audit.record_outcome(did, {"success": True})
        row = audit.get_by_id(did)
        assert row["outcome"] == {"success": True}
        assert row["outcome_at"] is not None

    def test_get_recent_filtered(self, audit):
        audit.record_decision("a1", "type_x", {}, {})
        audit.record_decision("a2", "type_y", {}, {})
        audit.record_decision("a1", "type_x", {}, {})

        rows = audit.get_recent(agent="a1")
        assert all(r["agent"] == "a1" for r in rows)
        assert len(rows) == 2

    def test_count(self, audit):
        assert audit.count() == 0
        audit.record_decision("a", "t", {}, {})
        audit.record_decision("a", "t", {}, {})
        assert audit.count() == 2
        assert audit.count(agent="a") == 2
        assert audit.count(decision_type="other") == 0

    def test_event_bus_publish_on_record(self, tmp_path):
        from openjarvis.core.decisions import DecisionAudit
        from openjarvis.core.events import EventBus

        bus = EventBus()
        received = []
        bus.subscribe("decision_recorded", lambda e: received.append(e))

        audit = DecisionAudit(db_path=str(tmp_path / "d2.db"), bus=bus)
        audit.record_decision("bot", "deploy", {}, {}, reasoning="test")
        # Give the bus a moment (it's synchronous)
        assert len(received) == 1


# ===========================================================================
# 5. openjarvis/daemon/service.py — plist/systemd generation
# ===========================================================================

class TestDaemonService:
    def test_generate_systemd_contains_python(self):
        from openjarvis.daemon.service import generate_systemd_service

        content = generate_systemd_service()
        assert sys.executable in content
        assert "openjarvis.daemon.gateway" in content
        assert "[Service]" in content

    def test_generate_launchd_contains_python(self):
        from openjarvis.daemon.service import generate_launchd_plist

        content = generate_launchd_plist()
        assert sys.executable in content
        assert "com.openjarvis.gateway" in content
        assert "<plist" in content

    def test_generate_systemd_writes_file(self, tmp_path):
        from openjarvis.daemon.service import generate_systemd_service

        out = tmp_path / "openjarvis.service"
        generate_systemd_service(output=out)
        assert out.exists()
        assert "[Unit]" in out.read_text()

    def test_generate_launchd_writes_file(self, tmp_path):
        from openjarvis.daemon.service import generate_launchd_plist

        out = tmp_path / "com.openjarvis.gateway.plist"
        generate_launchd_plist(output=out)
        assert out.exists()
        assert "<?xml" in out.read_text()


# ===========================================================================
# 6. openjarvis/scheduler/dead_letter.py — DeadLetterQueue
# ===========================================================================

class TestDeadLetterQueue:
    @pytest.fixture
    def dlq(self, tmp_path):
        from openjarvis.scheduler.dead_letter import DeadLetterQueue

        return DeadLetterQueue(db_path=str(tmp_path / "dlq.db"), max_retries=3)

    def test_no_quarantine_before_max(self, dlq):
        payload = {"job": "email"}
        assert dlq.record_failure("email_job", payload, "err1") is False
        assert dlq.record_failure("email_job", payload, "err2") is False
        assert dlq.count() == 0

    def test_quarantine_at_max_retries(self, dlq):
        payload = {"job": "email"}
        dlq.record_failure("email_job", payload, "err1")
        dlq.record_failure("email_job", payload, "err2")
        quarantined = dlq.record_failure("email_job", payload, "err3")
        assert quarantined is True
        assert dlq.count() == 1

    def test_list_quarantined(self, dlq):
        payload = {"job": "x"}
        for i in range(3):
            dlq.record_failure("job_x", payload, f"err{i}")
        items = dlq.list_quarantined()
        assert len(items) == 1
        assert items[0]["task_type"] == "job_x"
        assert len(items[0]["errors"]) == 3

    def test_retry(self, dlq):
        payload = {"job": "retry_me"}
        for i in range(3):
            dlq.record_failure("job_r", payload, f"e{i}")
        items = dlq.list_quarantined()
        item_id = items[0]["id"]
        result = dlq.retry(item_id)
        assert result is not None
        assert result["task_type"] == "job_r"
        # After retry it's no longer "quarantined"
        assert dlq.count() == 0

    def test_discard(self, dlq):
        payload = {"job": "discard_me"}
        for i in range(3):
            dlq.record_failure("job_d", payload, f"e{i}")
        items = dlq.list_quarantined()
        ok = dlq.discard(items[0]["id"])
        assert ok is True
        assert dlq.count() == 0

    def test_clear_failures_resets_counter(self, dlq):
        payload = {"job": "resettable"}
        dlq.record_failure("job_c", payload, "err1")
        dlq.clear_failures("job_c", payload)
        # After clearing, need 3 more failures to quarantine
        dlq.record_failure("job_c", payload, "err2")
        dlq.record_failure("job_c", payload, "err3")
        assert dlq.count() == 0  # not quarantined yet (only 2 since reset)

    def test_event_bus_alert_on_quarantine(self, tmp_path):
        from openjarvis.core.events import EventBus, EventType
        from openjarvis.scheduler.dead_letter import DeadLetterQueue

        bus = EventBus()
        alerts = []
        bus.subscribe(EventType.SECURITY_ALERT, lambda e: alerts.append(e))

        dlq = DeadLetterQueue(
            db_path=str(tmp_path / "dlq2.db"), bus=bus, max_retries=2
        )
        payload = {"job": "boom"}
        dlq.record_failure("j", payload, "e1")
        dlq.record_failure("j", payload, "e2")  # triggers quarantine
        assert len(alerts) == 1


# ===========================================================================
# 7. openjarvis/server/api_routes.py — key API routes via TestClient
# ===========================================================================

class TestApiRoutes:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from openjarvis.server.api_routes import (
            agents_router,
            budget_router,
            feedback_router,
            optimize_router,
            skills_router,
        )

        application = FastAPI()
        application.include_router(agents_router)
        application.include_router(budget_router)
        application.include_router(skills_router)
        application.include_router(feedback_router)
        application.include_router(optimize_router)
        return application

    def test_get_budget(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "usage" in data

    def test_set_budget_limits(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.put(
            "/v1/budget/limits",
            json={"max_tokens_per_day": 10000},
        )
        assert resp.status_code == 200
        assert resp.json()["limits"]["max_tokens_per_day"] == 10000

    def test_list_skills(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/skills")
        assert resp.status_code == 200
        assert "skills" in resp.json()

    def test_install_skill_not_implemented(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/v1/skills", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_implemented"

    def test_list_agents(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "registered" in data
        assert "running" in data

    def test_feedback_stats(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/feedback/stats")
        assert resp.status_code == 200

    def test_optimize_runs_no_db(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/optimize/runs")
        assert resp.status_code == 200
        assert "runs" in resp.json()

    def test_start_optimize_run(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/v1/optimize/runs",
            json={"benchmark": "test_bench"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"


# ===========================================================================
# 8. openjarvis/server/comparison.py — comparison page
# ===========================================================================

class TestComparisonPage:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from openjarvis.server.comparison import comparison_router

        app = FastAPI()
        app.include_router(comparison_router)
        return TestClient(app)

    def test_comparison_returns_200(self, client):
        resp = client.get("/comparison")
        assert resp.status_code == 200

    def test_comparison_is_html(self, client):
        resp = client.get("/comparison")
        assert "text/html" in resp.headers["content-type"]

    def test_comparison_contains_openjarvis(self, client):
        resp = client.get("/comparison")
        assert "OpenJarvis" in resp.text

    def test_comparison_contains_cloud_pricing(self, client):
        resp = client.get("/comparison")
        # Should mention the cloud providers in the comparison page
        assert "GPT" in resp.text or "Claude" in resp.text


# ===========================================================================
# 9. openjarvis/server/dashboard.py — dashboard route
# ===========================================================================

class TestDashboardPage:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from openjarvis.server.dashboard import dashboard_router

        app = FastAPI()
        app.include_router(dashboard_router)
        return TestClient(app)

    def test_dashboard_returns_200(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_dashboard_is_html(self, client):
        resp = client.get("/dashboard")
        assert "text/html" in resp.headers["content-type"]

    def test_dashboard_contains_expected_title(self, client):
        resp = client.get("/dashboard")
        assert "Savings Dashboard" in resp.text or "OpenJarvis" in resp.text

    def test_dashboard_shows_total_requests(self, client):
        resp = client.get("/dashboard")
        assert "Total Requests" in resp.text


# ===========================================================================
# 10. openjarvis/server/routes.py — core routes
# ===========================================================================

class TestCoreRoutes:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI

        from openjarvis.server.routes import router

        application = FastAPI()
        application.include_router(router)

        # Mock engine on app state
        engine = MagicMock()
        engine.health.return_value = True
        engine.list_models.return_value = ["llama3", "mistral"]
        engine.generate.return_value = {
            "content": "hello",
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        application.state.engine = engine
        application.state.model = "llama3"
        application.state.agent = None
        application.state.engine_name = "ollama"
        application.state.config = None
        application.state.memory_backend = None

        return application

    def test_health_ok(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_503_when_engine_unhealthy(self, app):
        from fastapi.testclient import TestClient

        app.state.engine.health.return_value = False
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 503

    def test_list_models(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        model_ids = [m["id"] for m in resp.json()["data"]]
        assert "llama3" in model_ids
        assert "mistral" in model_ids

    def test_server_info(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/v1/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data
        assert "engine" in data

    def test_chat_completions_non_streaming(self, app):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "llama3",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "hello"

    def test_list_channels_no_bridge(self, app):
        from fastapi.testclient import TestClient

        app.state.channel_bridge = None
        client = TestClient(app)
        resp = client.get("/v1/channels")
        assert resp.status_code == 200
        assert resp.json()["channels"] == []

    def test_savings_no_db(self, app):
        from fastapi.testclient import TestClient

        with patch("openjarvis.core.config.DEFAULT_CONFIG_DIR", Path("/nonexistent_dir_xyz")):
            client = TestClient(app)
            resp = client.get("/v1/savings")
        assert resp.status_code == 200


# ===========================================================================
# 11. openjarvis/server/stream_bridge.py — AgentStreamBridge
# ===========================================================================

class TestAgentStreamBridge:
    def _make_bridge(self, content="hello world"):
        from openjarvis.core.events import EventBus
        from openjarvis.server.models import ChatCompletionRequest, ChatMessage
        from openjarvis.server.stream_bridge import AgentStreamBridge

        bus = EventBus()
        agent = MagicMock()
        result = MagicMock()
        result.content = content
        result.tool_results = []
        result.metadata = {}
        agent.run.return_value = result
        agent._model = "llama3"

        req = ChatCompletionRequest(
            model="llama3",
            messages=[ChatMessage(role="user", content="say hi")],
        )
        return AgentStreamBridge(agent, bus, "llama3", req)

    def test_format_named_event(self):
        bridge = self._make_bridge()
        output = bridge._format_named_event("tool_call_start", {"tool": "calc"})
        assert output.startswith("event: tool_call_start\n")
        assert '"tool": "calc"' in output
        assert output.endswith("\n\n")

    def test_estimate_prompt_tokens(self):
        from openjarvis.server.models import ChatMessage
        from openjarvis.server.stream_bridge import _estimate_prompt_tokens

        msgs = [ChatMessage(role="user", content="a" * 400)]
        tokens = _estimate_prompt_tokens(msgs)
        assert tokens == 100  # 400 chars / 4

    def test_stream_yields_sse(self):
        """Stream produces SSE-formatted output including [DONE]."""
        bridge = self._make_bridge(content="foo bar")

        async def run():
            chunks = []
            async for chunk in bridge.stream():
                chunks.append(chunk)
            return chunks

        chunks = asyncio.get_event_loop().run_until_complete(run())
        combined = "".join(chunks)
        assert "data:" in combined
        assert "[DONE]" in combined

    def test_stream_with_tool_results(self):
        from openjarvis.core.events import EventBus
        from openjarvis.server.models import ChatCompletionRequest, ChatMessage
        from openjarvis.server.stream_bridge import AgentStreamBridge

        bus = EventBus()
        agent = MagicMock()
        result = MagicMock()
        result.content = "done"
        tr = MagicMock()
        tr.tool_name = "calculator"
        tr.success = True
        tr.content = "42"
        tr.latency_seconds = 0.1
        result.tool_results = [tr]
        result.metadata = {}
        agent.run.return_value = result
        agent._model = "llama3"

        req = ChatCompletionRequest(
            model="llama3",
            messages=[ChatMessage(role="user", content="calc")],
        )
        bridge = AgentStreamBridge(agent, bus, "llama3", req)

        async def run():
            return [c async for c in bridge.stream()]

        chunks = asyncio.get_event_loop().run_until_complete(run())
        combined = "".join(chunks)
        assert "tool_results" in combined


# ===========================================================================
# 12. openjarvis/vassals/backprop.py — _is_immutable and immutable constants
# ===========================================================================

class TestVassalBackprop:
    """Tests for the pure-logic portions of backprop (no DB/shared imports needed)."""

    def test_immutable_files_constant_exists(self):
        """IMMUTABLE_FILES set is defined and non-empty."""
        from openjarvis.vassals.backprop import IMMUTABLE_FILES  # type: ignore[import]

        assert isinstance(IMMUTABLE_FILES, (set, frozenset))
        assert len(IMMUTABLE_FILES) > 0

    def test_price_guardrails_constants(self):
        from openjarvis.vassals.backprop import (  # type: ignore[import]
            PRICE_MAX,
            PRICE_MAX_DELTA_PER_CYCLE,
            PRICE_MIN,
        )

        assert PRICE_MIN < PRICE_MAX
        assert PRICE_MAX_DELTA_PER_CYCLE > 0

    def test_is_immutable_blocks_protected_file(self):
        from openjarvis.vassals.backprop import _is_immutable  # type: ignore[import]

        assert _is_immutable("titan/compliance.py") is True

    def test_is_immutable_allows_normal_file(self):
        from openjarvis.vassals.backprop import _is_immutable  # type: ignore[import]

        assert _is_immutable("titan/pipeline.py") is False

    def test_is_immutable_blocks_tests_dir(self):
        from openjarvis.vassals.backprop import _is_immutable  # type: ignore[import]

        assert _is_immutable("tests/test_something.py") is True

    def test_max_proposals_constant(self):
        from openjarvis.vassals.backprop import MAX_PROPOSALS_PER_CYCLE  # type: ignore[import]

        assert MAX_PROPOSALS_PER_CYCLE > 0


# ===========================================================================
# 13. openjarvis/vassals/cell_division.py — proposal logic
# ===========================================================================

class TestVassalCellDivision:
    """Test pure logic constants and threshold calculations."""

    def test_constants_are_sane(self):
        from openjarvis.vassals.cell_division import (  # type: ignore[import]
            AGENT_ERROR_RATE_THRESHOLD,
            MIN_DAYS_BEFORE_PROPOSAL,
            REVENUE_CONCENTRATION_THRESHOLD,
            STAGE_ERROR_RATE_THRESHOLD,
        )

        assert 0 < STAGE_ERROR_RATE_THRESHOLD < 1
        assert 0 < AGENT_ERROR_RATE_THRESHOLD < 1
        assert 0 < REVENUE_CONCENTRATION_THRESHOLD < 1
        assert MIN_DAYS_BEFORE_PROPOSAL > 0

    def test_revenue_concentration_threshold_is_50_percent(self):
        from openjarvis.vassals.cell_division import (
            REVENUE_CONCENTRATION_THRESHOLD,  # type: ignore[import]
        )

        assert REVENUE_CONCENTRATION_THRESHOLD == 0.50


# ===========================================================================
# 14. openjarvis/vassals/discovery.py — VassalDiscovery
# ===========================================================================

class TestVassalDiscovery:
    def _make_discovery(self, config=None):
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        return VassalDiscovery(bus=bus, config=config or {})

    def test_discover_one_success(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        card = AgentCard(
            name="Titan",
            description="revenue engine",
            capabilities=["pipeline_status", "health"],
        )
        bus = EventBus()
        disc = VassalDiscovery(bus=bus)

        with patch("openjarvis.a2a.client.A2AClient.discover", return_value=card):
            info = disc.discover_one("titan", "http://localhost:9001")

        assert info is not None
        assert info.healthy is True
        assert "pipeline_status" in info.capabilities
        assert "Titan" in disc.vassals

    def test_discover_one_failure_marks_unhealthy(self):
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        disc = VassalDiscovery(bus=bus)

        with patch("openjarvis.a2a.client.A2AClient.discover",
                   side_effect=ConnectionError("refused")):
            info = disc.discover_one("hermes", "http://localhost:9002")

        assert info is None
        assert disc.vassals["hermes"].healthy is False
        assert disc.vassals["hermes"].last_error != ""

    def test_discover_all_processes_config(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        cfg = {
            "titan": {"url": "http://localhost:9001"},
            "hermes": {"url": "http://localhost:9002"},
        }
        disc = VassalDiscovery(bus=bus, config=cfg)

        # Return distinct names so they don't overwrite each other in the dict
        cards = [
            AgentCard(name="titan", description=""),
            AgentCard(name="hermes", description=""),
        ]
        call_count = [0]

        def side_effect(*a, **kw):
            idx = call_count[0]
            call_count[0] += 1
            return cards[idx % len(cards)]

        with patch("openjarvis.a2a.client.A2AClient.discover", side_effect=side_effect):
            result = disc.discover_all()

        assert len(result) == 2

    def test_discover_all_skips_empty_url(self):
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        cfg = {"noop": {"url": ""}}
        disc = VassalDiscovery(bus=bus, config=cfg)
        result = disc.discover_all()
        assert len(result) == 0

    def test_list_all_tools(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        disc = VassalDiscovery(bus=bus)
        card = AgentCard(name="Titan", capabilities=["health", "status"])

        with patch("openjarvis.a2a.client.A2AClient.discover", return_value=card):
            disc.discover_one("titan", "http://localhost:9001")

        tools = disc.list_all_tools()
        assert "Titan.health" in tools
        assert "Titan.status" in tools
        assert "Titan.ask" in tools

    def test_summary(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalDiscovery

        bus = EventBus()
        disc = VassalDiscovery(bus=bus)
        card = AgentCard(name="Titan", description="rev engine", capabilities=["go"])

        with patch("openjarvis.a2a.client.A2AClient.discover", return_value=card):
            disc.discover_one("t", "http://localhost:9001")

        s = disc.summary()
        assert "Titan" in s
        assert s["Titan"]["healthy"] is True


# ===========================================================================
# 15. openjarvis/vassals/event_relay.py — EventRelay
# ===========================================================================

class TestEventRelay:
    def _make_relay(self, vassals=None):
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.event_relay import EventRelay

        bus = EventBus()
        discovery = MagicMock()
        discovery.vassals = vassals or {}
        relay = EventRelay(bus=bus, vassal_discovery=discovery, poll_interval=999.0)
        return relay, bus, discovery

    def test_ingest_remote_event_publishes_to_bus(self):
        from openjarvis.core.events import EventType

        relay, bus, _ = self._make_relay()
        received = []
        bus.subscribe(EventType.CUSTOM, lambda e: received.append(e))

        relay._ingest_remote_event("titan", {
            "event_type": "pipeline_complete",
            "payload": {"result": "ok"},
            "created_at": "2026-01-01T00:00:00",
        })

        assert len(received) == 1
        assert received[0].data["sub_type"] == "vassal_event"
        assert received[0].data["source"] == "titan"

    def test_ingest_deduplicates(self):
        relay, bus, _ = self._make_relay()
        received = []
        from openjarvis.core.events import EventType
        bus.subscribe(EventType.CUSTOM, lambda e: received.append(e))

        event_data = {
            "event_type": "test_event",
            "payload": {},
            "created_at": "2026-01-01T00:00:00",
        }
        relay._ingest_remote_event("titan", event_data)
        relay._ingest_remote_event("titan", event_data)  # duplicate

        assert len(received) == 1

    def test_relay_to_vassal_skips_unhealthy(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalInfo
        from openjarvis.vassals.event_relay import EventRelay

        bus = EventBus()
        vassal_info = VassalInfo(
            card=AgentCard(name="hermes"),
            client=MagicMock(),
            url="http://localhost:9002",
            healthy=False,
        )
        discovery = MagicMock()
        discovery.get.return_value = vassal_info
        relay = EventRelay(bus=bus, vassal_discovery=discovery)

        relay.relay_to_vassal("hermes", "test_event", {})
        vassal_info.client.send_task.assert_not_called()

    def test_relay_to_all_calls_healthy_vassals(self):
        from openjarvis.a2a.protocol import AgentCard
        from openjarvis.core.events import EventBus
        from openjarvis.vassals.discovery import VassalInfo
        from openjarvis.vassals.event_relay import EventRelay

        bus = EventBus()
        client_mock = MagicMock()
        client_mock.send_task.return_value = MagicMock(output_text="ok")

        vassal_info = VassalInfo(
            card=AgentCard(name="titan"),
            client=client_mock,
            url="http://localhost:9001",
            healthy=True,
        )
        discovery = MagicMock()
        discovery.vassals = {"titan": vassal_info}
        discovery.get.return_value = vassal_info
        relay = EventRelay(bus=bus, vassal_discovery=discovery)

        relay.relay_to_all("alert", {"msg": "hello"})
        client_mock.send_task.assert_called_once()


# ===========================================================================
# 16. openjarvis/vassals/infra_health.py — service status classification
# ===========================================================================

class TestInfraHealth:
    def test_is_service_ok_with_ok_status(self):
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        health = {"postgres": {"status": "ok"}, "checked_at": 1234}
        assert is_service_ok(health, "postgres") is True

    def test_is_service_ok_with_recovered_status(self):
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        health = {"ollama": {"status": "recovered"}}
        assert is_service_ok(health, "ollama") is True

    def test_is_service_ok_with_not_configured(self):
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        health = {"instantly": {"status": "not_configured"}}
        assert is_service_ok(health, "instantly") is True

    def test_is_service_ok_with_down_status(self):
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        health = {"ollama": {"status": "down", "error": "refused"}}
        assert is_service_ok(health, "ollama") is False

    def test_is_service_ok_with_degraded_status(self):
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        health = {"mem0": {"status": "degraded"}}
        assert is_service_ok(health, "mem0") is False

    def test_is_service_ok_fails_open_on_missing(self):
        """When health data is missing entirely, fail open (return True)."""
        from openjarvis.vassals.infra_health import is_service_ok  # type: ignore[import]

        assert is_service_ok({}, "postgres") is True
        assert is_service_ok(None, "postgres") is True  # type: ignore[arg-type]

    def test_check_disk_space_ok(self):
        """_check_disk_space returns a dict with expected keys."""
        from openjarvis.vassals.infra_health import _check_disk_space  # type: ignore[import]

        result = asyncio.get_event_loop().run_until_complete(_check_disk_space())
        assert "status" in result
        assert result["status"] in ("ok", "degraded", "down")
        assert "percent_used" in result
        assert "free_gb" in result
