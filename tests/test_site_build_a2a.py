"""Tests for ClawdBot site building A2A capability wiring — Phase 1.

Verifies:
1. build_demo_site and build_full_site are registered in CAPABILITY_HANDLERS
2. The A2A handlers call the real site_builder functions (mocked)
3. close_deal._build_demo_site uses call_agent_capability, not ask_agent
4. build_site._build_full_site uses call_agent_capability, not ask_agent
5. Task routing maps build_demo_site and build_full_site to clawdbot
"""

import asyncio
import importlib
import inspect
import sys
import types
from unittest.mock import AsyncMock, patch

# ── 1. Capability registration ──

def test_build_demo_site_in_capability_handlers():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    assert "build_demo_site" in CAPABILITY_HANDLERS, \
        f"build_demo_site not in CAPABILITY_HANDLERS: {list(CAPABILITY_HANDLERS.keys())}"


def test_build_full_site_in_capability_handlers():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    assert "build_full_site" in CAPABILITY_HANDLERS, \
        f"build_full_site not in CAPABILITY_HANDLERS: {list(CAPABILITY_HANDLERS.keys())}"


def test_build_capabilities_in_agent_card():
    from clawdbot.a2a_server import CLAWDBOT_CARD
    assert "build_demo_site" in CLAWDBOT_CARD.capabilities
    assert "build_full_site" in CLAWDBOT_CARD.capabilities


# ── 2. A2A handlers call real site_builder ──

def test_a2a_build_demo_site_calls_site_builder():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    handler = CAPABILITY_HANDLERS["build_demo_site"]

    lead = {"id": 1, "business_name": "Test Plumbing", "industry": "plumbing"}

    with patch("clawdbot.site_builder.build_demo_site", new_callable=AsyncMock, return_value="https://test.netlify.app") as mock_build:
        result = asyncio.run(handler(lead=lead))

    mock_build.assert_awaited_once_with(lead)
    assert result["url"] == "https://test.netlify.app"
    assert result["status"] == "built"


def test_a2a_build_full_site_calls_site_builder():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    handler = CAPABILITY_HANDLERS["build_full_site"]

    lead = {"id": 2, "business_name": "Test Bakery", "industry": "food"}

    with patch("clawdbot.site_builder.build_full_site", new_callable=AsyncMock, return_value="https://bakery.netlify.app") as mock_build:
        result = asyncio.run(handler(lead=lead))

    mock_build.assert_awaited_once_with(lead)
    assert result["url"] == "https://bakery.netlify.app"
    assert result["status"] == "built"


def test_a2a_build_demo_site_returns_error_on_failure():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    handler = CAPABILITY_HANDLERS["build_demo_site"]

    with patch("clawdbot.site_builder.build_demo_site", new_callable=AsyncMock, side_effect=RuntimeError("build exploded")):
        result = asyncio.run(handler(lead={"id": 1}))

    assert result["status"] == "error"
    assert "build exploded" in result["error"]
    assert result["url"] == ""


# ── 3. close_deal uses call_agent_capability ──

def test_close_deal_uses_call_agent_capability():
    """close_deal._build_demo_site must call call_agent_capability, not ask_agent."""
    # Set up fakes for import
    modules_to_fake = [
        "shared.db", "shared.llm_client", "shared.pipeline_alerts",
        "titan.state_machine", "titan.memory", "titan.training",
        "titan.pipeline.close_deal",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())
    fake_db.transaction = AsyncMock()

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.close_deal", None)

    try:
        cd_mod = importlib.import_module("titan.pipeline.close_deal")
        source = inspect.getsource(cd_mod._build_demo_site)
        assert "call_agent_capability" in source, \
            f"_build_demo_site should use call_agent_capability, not ask_agent. Source:\n{source}"
        assert "ask_agent" not in source, \
            f"_build_demo_site should NOT use ask_agent. Source:\n{source}"
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)


# ── 4. build_site uses call_agent_capability ──

def test_build_site_uses_call_agent_capability():
    """build_site._build_full_site must call call_agent_capability, not ask_agent."""
    modules_to_fake = [
        "shared.db", "shared.pipeline_alerts",
        "titan.state_machine", "titan.training",
        "titan.pipeline.build_site",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.build_site", None)

    try:
        bs_mod = importlib.import_module("titan.pipeline.build_site")
        source = inspect.getsource(bs_mod._build_full_site)
        assert "call_agent_capability" in source, \
            f"_build_full_site should use call_agent_capability. Source:\n{source}"
        assert "ask_agent" not in source, \
            f"_build_full_site should NOT use ask_agent. Source:\n{source}"
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)


# ── 5. Task routing ──

def test_task_routing_includes_build_capabilities():
    from shared.task_routing import TASK_ROUTING
    assert TASK_ROUTING.get("build_demo_site") == "clawdbot", \
        f"build_demo_site should route to clawdbot: {TASK_ROUTING.get('build_demo_site')}"
    assert TASK_ROUTING.get("build_full_site") == "clawdbot", \
        f"build_full_site should route to clawdbot: {TASK_ROUTING.get('build_full_site')}"


# ── 6. Behavioral: close_deal._build_demo_site calls the right capability ──

def test_close_deal_build_demo_site_calls_clawdbot_capability():
    """_build_demo_site must call call_agent_capability('clawdbot', 'build_demo_site', ...)."""
    modules_to_fake = [
        "shared.db", "shared.pipeline_alerts",
        "titan.state_machine", "titan.memory", "titan.training",
        "titan.pipeline.close_deal",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())
    fake_db.transaction = AsyncMock()

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.close_deal", None)

    try:
        cd_mod = importlib.import_module("titan.pipeline.close_deal")

        mock_cap = AsyncMock(return_value={"url": "https://demo.netlify.app", "status": "built"})

        lead = {"id": 1, "business_name": "TestCo", "email": "t@co.com"}

        with patch("shared.comms.call_agent_capability", mock_cap):
            url = asyncio.run(cd_mod._build_demo_site(lead))

        mock_cap.assert_awaited_once_with(
            "clawdbot", "build_demo_site", {"lead": lead}, timeout=120,
        )
        assert url == "https://demo.netlify.app"
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)
