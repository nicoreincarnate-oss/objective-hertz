"""Tests for configurable follow-up timing — Phase 3.

Verifies:
1. Default timing is [3, 3, 7, 14, 21, 28, 35] days
2. Custom timing from system_config is respected
3. Step-specific prompt angles are injected
4. Leads are filtered by per-step delay, not hardcoded 3 days
"""

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch


def _setup_fakes():
    modules_to_fake = [
        "shared.db", "shared.llm_client", "shared.pipeline_alerts",
        "titan.state_machine", "titan.memory", "titan.training",
        "titan.pipeline.follow_up",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db._config = {}
    async def get_config(key, default=None):
        return fake_db._config.get(key, default)
    async def set_config(key, value):
        fake_db._config[key] = value
    fake_db.get_config = get_config
    fake_db.set_config = set_config
    fake_db.emit_event = AsyncMock(return_value=1)
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value=None)
    fake_db.fetch_val = AsyncMock(return_value=None)
    fake_db.insert_task = AsyncMock(return_value=1)
    fake_db.increment_config_int = AsyncMock(return_value=1)
    fake_db.transaction = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"subject": "Follow up", "body": "Just checking in"}'),
        classify=AsyncMock(return_value="interested"),
    )

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")
    fake_memory.format_rules_for_prompt = AsyncMock(return_value="")
    fake_memory.attribute_reply_cause = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()
    fake_training.collect_email_outcome = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.follow_up", None)

    fu_mod = importlib.import_module("titan.pipeline.follow_up")
    return saved, fu_mod, fake_db


def _restore(saved):
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


def test_default_timing_exists():
    """DEFAULT_FOLLOW_UP_TIMING_DAYS must be a 7-element list."""
    saved, fu_mod, _ = _setup_fakes()
    try:
        assert hasattr(fu_mod, "DEFAULT_FOLLOW_UP_TIMING_DAYS")
        timing = fu_mod.DEFAULT_FOLLOW_UP_TIMING_DAYS
        assert len(timing) == 7, f"Expected 7 steps, got {len(timing)}"
        assert timing[0] == 3, f"Step 1 should be 3 days, got {timing[0]}"
        assert timing[2] == 7, f"Step 3 should be 7 days, got {timing[2]}"
        assert timing[3] == 14, f"Step 4 should be 14 days, got {timing[3]}"
    finally:
        _restore(saved)


def test_custom_timing_from_config():
    """system_config follow_up_timing_days overrides defaults."""
    saved, fu_mod, fake_db = _setup_fakes()
    try:
        fake_db._config["follow_up_timing_days"] = [1, 2, 5, 10, 20]
        delay = asyncio.run(fu_mod._get_follow_up_delay_days(2))
        assert delay == 5, f"Step 2 with custom config should be 5, got {delay}"
    finally:
        _restore(saved)


def test_default_timing_used_when_no_config():
    """Without system_config, defaults are used."""
    saved, fu_mod, fake_db = _setup_fakes()
    try:
        # No config set
        delay = asyncio.run(fu_mod._get_follow_up_delay_days(3))
        assert delay == 14, f"Step 3 default should be 14, got {delay}"
    finally:
        _restore(saved)


def test_step_angles_exist():
    """STEP_ANGLES must exist with 7 entries."""
    saved, fu_mod, _ = _setup_fakes()
    try:
        assert hasattr(fu_mod, "STEP_ANGLES")
        assert len(fu_mod.STEP_ANGLES) == 7, f"Expected 7 angles, got {len(fu_mod.STEP_ANGLES)}"
    finally:
        _restore(saved)


def test_send_follow_ups_respects_per_step_delay():
    """A lead at step 3 (14-day delay) should NOT get a follow-up after only 5 days."""
    saved, fu_mod, fake_db = _setup_fakes()
    try:
        now = datetime.now(timezone.utc)

        # Lead at follow_up_count=3 → needs 14-day delay
        # Last contact 5 days ago → should NOT be followed up
        lead_too_soon = {
            "id": 1, "business_name": "TooSoon Co", "email": "a@co.com",
            "follow_up_count": 3, "lead_score": 80,
            "last_contact_at": (now - timedelta(days=5)).isoformat(),
            "research_summary": "Test", "language": "en",
        }
        # Lead at follow_up_count=0 → needs 3-day delay
        # Last contact 4 days ago → SHOULD be followed up
        lead_ready = {
            "id": 2, "business_name": "Ready Co", "email": "b@co.com",
            "follow_up_count": 0, "lead_score": 70,
            "last_contact_at": (now - timedelta(days=4)).isoformat(),
            "research_summary": "Test", "language": "en",
        }

        fu_mod.fetch_all = AsyncMock(return_value=[lead_too_soon, lead_ready])

        composed = []
        original_compose = fu_mod._compose_and_queue_follow_up

        async def tracking_compose(lead):
            composed.append(lead["id"])

        with patch.object(fu_mod, "_compose_and_queue_follow_up", tracking_compose), \
             patch.object(fu_mod, "_should_follow_up", AsyncMock(return_value=True)):
            asyncio.run(fu_mod._send_follow_ups())

        # Lead 1 (5 days, needs 14) should NOT be composed
        assert 1 not in composed, f"Lead 1 should not be followed up after only 5 days (needs 14)"
        # Lead 2 (4 days, needs 3) SHOULD be composed
        assert 2 in composed, f"Lead 2 should be followed up after 4 days (needs 3)"
    finally:
        _restore(saved)


def test_angle_injected_into_prompt():
    """The compose prompt must include the step-specific angle."""
    saved, fu_mod, fake_db = _setup_fakes()
    try:
        prompts_captured = []
        original_generate = fu_mod.llm.generate

        async def capture_generate(prompt, **kwargs):
            prompts_captured.append(prompt)
            return '{"subject": "Follow up", "body": "Just checking in on our conversation."}'

        fu_mod.llm = types.SimpleNamespace(generate=capture_generate)
        fake_db.fetch_one = AsyncMock(return_value={"id": 99})

        lead = {"id": 1, "business_name": "TestCo", "email": "t@co.com",
                "follow_up_count": 2, "language": "en"}  # step 3

        asyncio.run(fu_mod._compose_and_queue_follow_up(lead))

        assert len(prompts_captured) >= 1, "LLM generate should have been called"
        prompt = prompts_captured[0]
        # Step 3 (index 2) = "curiosity"
        assert "curiosity" in prompt.lower(), \
            f"Step 3 prompt should include 'curiosity' angle, got: {prompt[:200]}"
    finally:
        _restore(saved)
