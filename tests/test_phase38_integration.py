"""Tests for Phase 38 — end-to-end integration of the V2 visual production pipeline.

Pre-mocks heavy external dependencies (psycopg_pool, psycopg, etc.) so tests
run without Postgres or other infra.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pre-mock heavy/unavailable modules so clawdbot.site_builder can import
# ---------------------------------------------------------------------------
_MOCKED_MODULES = {}


def _ensure_mock_module(name: str) -> MagicMock:
    """Insert a MagicMock into sys.modules if the module isn't available."""
    if name in sys.modules:
        return sys.modules[name]
    mock = MagicMock()
    _MOCKED_MODULES[name] = mock
    sys.modules[name] = mock
    return mock


# Mock the heavy infra that site_builder.py imports transitively
for _mod in [
    "psycopg_pool",
    "psycopg",
    "psycopg.rows",
    "openjarvis.vassals.registry",
    "shared.logging_config",
]:
    _ensure_mock_module(_mod)

# Ensure shared.db has the needed async helpers
_db_mock = _ensure_mock_module("shared.db")
_db_mock.emit_event = AsyncMock()
_db_mock.init_pool = AsyncMock()
_db_mock.close_pool = AsyncMock()
_db_mock.get_config = AsyncMock(return_value=None)
_db_mock.fetch_one = AsyncMock(return_value=None)
_db_mock.fetch_all = AsyncMock(return_value=[])
_db_mock.fetch_val = AsyncMock(return_value=None)
_db_mock.execute = AsyncMock()

# shared.comms needs record_decision + emit_event
_comms_mock = _ensure_mock_module("shared.comms")
_comms_mock.record_decision = AsyncMock()

# shared.config
_config_mock = _ensure_mock_module("shared.config")
_config_mock.config = {}

# shared.llm_client
_llm_mock = _ensure_mock_module("shared.llm_client")
_llm_mock.llm = AsyncMock(return_value="")

# clawdbot.design_sources
_ds_mock = _ensure_mock_module("clawdbot.design_sources")
_ds_mock._extract_research_facts = MagicMock(return_value=[])

# Now we can safely import
import clawdbot.site_builder as sb  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lead(**overrides):
    base = {
        "id": "lead-001",
        "business_name": "Acme Plumbing",
        "industry": "plumbing",
        "city": "Austin",
        "country": "US",
        "contact_name": "Jane Doe",
        "research_summary": "A local plumber with 10 years experience.",
    }
    base.update(overrides)
    return base


def _fake_build_plan():
    return SimpleNamespace(
        total_sections=4,
        direction_name="minimal-geometric",
        tier=SimpleNamespace(name="demo"),
        design_tokens={"primary": "#000"},
        sections=[],
    )


def _fake_build_result(section_count=4):
    sections = [
        SimpleNamespace(html=f"<section>{i}</section>")
        for i in range(section_count)
    ]
    return SimpleNamespace(
        sections=sections,
        total_time_s=12.5,
        total_cost_usd=0.03,
    )


def _fake_qa_result(passed=True, score=0.92):
    return SimpleNamespace(
        passed=passed,
        overall_score=score,
        issues=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestV2FlagRouting:
    """Verify the feature flag routes to v1 or v2."""

    @pytest.mark.asyncio
    async def test_v2_flag_routing(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")
        with patch.object(
            sb, "_build_site_v2", new_callable=AsyncMock,
            return_value="https://v2.example.com",
        ) as mock_v2:
            result = await sb._build_site(
                _make_lead(), site_type="demo", page_count=1,
            )
            mock_v2.assert_awaited_once()
            assert result == "https://v2.example.com"

    @pytest.mark.asyncio
    async def test_v1_flag_routing(self, monkeypatch):
        monkeypatch.delenv("CLAWDBOT_V2_ENABLED", raising=False)
        with patch.object(
            sb, "_build_site_v1", new_callable=AsyncMock,
            return_value="https://v1.example.com",
        ) as mock_v1:
            result = await sb._build_site(
                _make_lead(), site_type="demo", page_count=1,
            )
            mock_v1.assert_awaited_once()
            assert result == "https://v1.example.com"


class TestV2FullFlow:

    @pytest.mark.asyncio
    async def test_v2_full_flow(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")

        plan = _fake_build_plan()
        build_result = _fake_build_result()
        qa_result = _fake_qa_result(passed=True, score=0.95)
        final_html = "<html><body>Hello</body></html>"

        # Mock all phase 33-37 modules
        sp = MagicMock()
        sp.generate_build_plan = AsyncMock(return_value=plan)
        so = MagicMock()
        so.build_all_sections = AsyncMock(return_value=build_result)
        pa = MagicMock()
        pa.assemble_page = AsyncMock(return_value=final_html)
        pa.assemble_multipage_site = AsyncMock(return_value={})
        fq = MagicMock()
        fq.run_full_page_qa = AsyncMock(return_value=qa_result)
        fq.deploy_gate = AsyncMock(return_value=(True, "ok"))

        monkeypatch.setitem(sys.modules, "clawdbot.section_planner", sp)
        monkeypatch.setitem(sys.modules, "clawdbot.section_orchestrator", so)
        monkeypatch.setitem(sys.modules, "clawdbot.page_assembler", pa)
        monkeypatch.setitem(sys.modules, "clawdbot.fullpage_qa", fq)

        with (
            patch.object(sb, "_get_playwright_pool", return_value=None),
            patch.object(sb, "emit_event", new_callable=AsyncMock),
            patch.object(
                sb, "_deploy_to_v0", new_callable=AsyncMock,
                return_value="https://deployed.example.com",
            ),
        ):
            url = await sb._build_site_v2(
                _make_lead(), site_type="demo", page_count=1,
            )
            assert url == "https://deployed.example.com"


class TestV2Fallback:

    @pytest.mark.asyncio
    async def test_v2_fallback_on_error(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")

        sp = MagicMock()
        sp.generate_build_plan = AsyncMock(
            side_effect=RuntimeError("planner broke"),
        )
        monkeypatch.setitem(sys.modules, "clawdbot.section_planner", sp)

        with (
            patch.object(sb, "emit_event", new_callable=AsyncMock),
            patch.object(
                sb, "_build_site_v1", new_callable=AsyncMock,
                return_value="https://v1-fallback.example.com",
            ) as mock_v1,
        ):
            result = await sb._build_site_v2(
                _make_lead(), site_type="demo", page_count=1,
            )
            assert result == "https://v1-fallback.example.com"
            mock_v1.assert_awaited_once()


class TestPlaywrightPool:

    def test_pool_getter_returns_none_by_default(self):
        sb._set_playwright_pool(None)
        assert sb._get_playwright_pool() is None

    def test_pool_getter_returns_injected_pool(self):
        sentinel = object()
        sb._set_playwright_pool(sentinel)
        try:
            assert sb._get_playwright_pool() is sentinel
        finally:
            sb._set_playwright_pool(None)


class TestEventEmission:

    @pytest.mark.asyncio
    async def test_event_emission(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")

        emitted: list[str] = []

        async def _capture(name, data=None):
            emitted.append(name)

        plan = _fake_build_plan()
        build_result = _fake_build_result()
        qa_result = _fake_qa_result(passed=True)

        sp = MagicMock()
        sp.generate_build_plan = AsyncMock(return_value=plan)
        so = MagicMock()
        so.build_all_sections = AsyncMock(return_value=build_result)
        pa = MagicMock()
        pa.assemble_page = AsyncMock(return_value="<html></html>")
        pa.assemble_multipage_site = AsyncMock(return_value={})
        fq = MagicMock()
        fq.run_full_page_qa = AsyncMock(return_value=qa_result)
        fq.deploy_gate = AsyncMock(return_value=(True, "ok"))

        monkeypatch.setitem(sys.modules, "clawdbot.section_planner", sp)
        monkeypatch.setitem(sys.modules, "clawdbot.section_orchestrator", so)
        monkeypatch.setitem(sys.modules, "clawdbot.page_assembler", pa)
        monkeypatch.setitem(sys.modules, "clawdbot.fullpage_qa", fq)

        with (
            patch.object(sb, "_get_playwright_pool", return_value=None),
            patch.object(sb, "emit_event", side_effect=_capture),
            patch.object(
                sb, "_deploy_to_v0", new_callable=AsyncMock,
                return_value="https://test.example.com",
            ),
        ):
            await sb._build_site_v2(
                _make_lead(), site_type="demo", page_count=1,
            )

        assert "site_build_v2_started" in emitted
        assert "site_build_v2_completed" in emitted


class TestDeployGate:

    @pytest.mark.asyncio
    async def test_deploy_gate_blocks(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")

        plan = _fake_build_plan()
        build_result = _fake_build_result()
        qa_result = _fake_qa_result(passed=False, score=0.3)

        sp = MagicMock()
        sp.generate_build_plan = AsyncMock(return_value=plan)
        so = MagicMock()
        so.build_all_sections = AsyncMock(return_value=build_result)
        pa = MagicMock()
        pa.assemble_page = AsyncMock(return_value="<html></html>")
        fq = MagicMock()
        fq.run_full_page_qa = AsyncMock(return_value=qa_result)
        fq.deploy_gate = AsyncMock(return_value=(False, "QA score too low"))

        monkeypatch.setitem(sys.modules, "clawdbot.section_planner", sp)
        monkeypatch.setitem(sys.modules, "clawdbot.section_orchestrator", so)
        monkeypatch.setitem(sys.modules, "clawdbot.page_assembler", pa)
        monkeypatch.setitem(sys.modules, "clawdbot.fullpage_qa", fq)

        with (
            patch.object(sb, "_get_playwright_pool", return_value=None),
            patch.object(sb, "emit_event", new_callable=AsyncMock),
        ):
            result = await sb._build_site_v2(
                _make_lead(), site_type="demo", page_count=1,
            )
            assert result == ""


class TestMultiPageFlow:

    @pytest.mark.asyncio
    async def test_multipage_flow(self, monkeypatch):
        monkeypatch.setenv("CLAWDBOT_V2_ENABLED", "true")

        plan = _fake_build_plan()
        build_result = _fake_build_result()
        qa_result = _fake_qa_result(passed=True)
        pages = {
            "index.html": "<html>home</html>",
            "about.html": "<html>about</html>",
        }

        sp = MagicMock()
        sp.generate_build_plan = AsyncMock(return_value=plan)
        so = MagicMock()
        so.build_all_sections = AsyncMock(return_value=build_result)
        pa = MagicMock()
        pa.assemble_page = AsyncMock(return_value="<html></html>")
        pa.assemble_multipage_site = AsyncMock(return_value=pages)
        fq = MagicMock()
        fq.run_full_page_qa = AsyncMock(return_value=qa_result)
        fq.deploy_gate = AsyncMock(return_value=(True, "ok"))

        deploy_mod = MagicMock()
        deploy_mod.deploy_static_site = AsyncMock(
            return_value="https://multi.example.com",
        )

        monkeypatch.setitem(sys.modules, "clawdbot.section_planner", sp)
        monkeypatch.setitem(sys.modules, "clawdbot.section_orchestrator", so)
        monkeypatch.setitem(sys.modules, "clawdbot.page_assembler", pa)
        monkeypatch.setitem(sys.modules, "clawdbot.fullpage_qa", fq)
        monkeypatch.setitem(sys.modules, "clawdbot.deploy", deploy_mod)

        with (
            patch.object(sb, "_get_playwright_pool", return_value=None),
            patch.object(sb, "emit_event", new_callable=AsyncMock),
        ):
            url = await sb._build_site_v2(
                _make_lead(), site_type="full", page_count=5,
            )
            assert url == "https://multi.example.com"
            pa.assemble_multipage_site.assert_awaited_once()
