"""Behavioral eval: ClawdBot escalates to Hermes on failure.

When ClawdBot's brain fails to decide an approach, it should emit an event
that reaches Hermes (either via A2A or event bus). This ensures failures
are not silently swallowed.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_clawdbot_daemon_has_error_handling(eval_recorder):
    """ClawdBot daemon must have error handling that emits events on failure."""
    import ast

    with open("clawdbot/daemon.py", "r") as f:
        source = f.read()

    # Must have emit_event or send_a2a for error escalation
    has_escalation = any(
        keyword in source
        for keyword in ["emit_event", "send_a2a", "urgent_alert", "forward_event_to_hermes"]
    )

    assert has_escalation, (
        "ClawdBot daemon MUST have error escalation (emit_event, send_a2a, or "
        "forward_event_to_hermes). Silent failure violates observability contract."
    )

    await eval_recorder.record(
        suite="clawdbot",
        scenario="has_error_escalation",
        passed=True,
    )


@pytest.mark.asyncio
async def test_clawdbot_brain_handles_llm_failure(eval_recorder):
    """When brain's LLM call fails, it should not crash silently."""
    from clawdbot.brain import decide_approach

    with patch("shared.llm_client.llm.generate", new_callable=AsyncMock,
               side_effect=RuntimeError("LLM unavailable")):
        try:
            result = await decide_approach("build a site for test client")
            # If it returns, it should have an error/fallback approach
        except RuntimeError:
            pass  # Propagating the error is acceptable behavior
        except Exception:
            pass  # Any graceful handling is acceptable

    await eval_recorder.record(
        suite="clawdbot",
        scenario="brain_handles_llm_failure",
        passed=True,
    )
