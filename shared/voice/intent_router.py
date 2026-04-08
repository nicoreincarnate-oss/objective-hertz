"""Voice intent router — Hermes/Jarvis voice loop.

Takes a transcribed voice command and routes it to the right Perseus daemon
or skill. Uses local Qwen3-30B-A3B for intent disambiguation.

Pipeline:
  1. Parakeet transcribes audio → text
  2. VoiceIntentRouter parses intent → daemon + action + params
  3. Daemon executes action via existing A2A capability
  4. Response synthesized via Kokoro and played back
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from shared.tiers import TierName

logger = logging.getLogger("perseus.voice.intent")


@dataclass
class VoiceIntent:
    daemon: str                    # Which daemon to dispatch to
    action: str                    # Capability/action name
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    raw_transcript: str = ""
    needs_confirmation: bool = False
    natural_response: str = ""     # Pre-formatted response (e.g. "Got it, checking pipeline...")


INTENT_PROMPT = """You are the Perseus voice intent router. Map a spoken command to the right daemon and action.

Daemons + actions:
- titan:    pipeline_status, lead_count, deal_count, pause_outreach, resume_outreach, stage_breakdown
- hermes:   show_briefing, alert_summary, jarvis_mode_on, jarvis_mode_off, send_message_to
- clawdbot: site_status, build_count, last_built_site, browser_status
- conway:   wallet_balance, monthly_spend, recent_transactions
- deerflow: latest_brief, source_count, top_sources_today
- ruflo:    fix_status, recent_patches, queue_depth
- perseus:  health_status, scheduler_state, restart_daemon, pause_daemon, resume_daemon
- openjarvis: workflow_status, recent_decisions

Voice command: "{transcript}"

Output ONLY this JSON (no prose):
{{
  "daemon": "titan|hermes|clawdbot|conway|deerflow|ruflo|perseus|openjarvis",
  "action": "action_name",
  "params": {{"key": "value"}},
  "confidence": 0.0-1.0,
  "natural_response": "What to speak back immediately while we fetch the answer",
  "needs_confirmation": false
}}

If the command is destructive (pause, restart, send), set needs_confirmation=true.
If you can't map it, return daemon="hermes", action="clarify", confidence=0.3.
"""


class VoiceIntentRouter:
    def __init__(self, llm_client: Any, classifier_tier: TierName = TierName.LOCAL):
        self.llm = llm_client
        self.tier = classifier_tier

    async def route(self, transcript: str) -> VoiceIntent:
        """Map a voice transcript to a daemon action."""
        if not transcript.strip():
            return VoiceIntent(
                daemon="hermes",
                action="clarify",
                confidence=0.0,
                raw_transcript=transcript,
                natural_response="I didn't catch that. Could you repeat?",
            )

        prompt = INTENT_PROMPT.format(transcript=transcript)
        try:
            response = await self.llm.generate(
                prompt,
                model=self.tier.value,
                daemon_name="hermes",
                pipeline_stage="jarvis_voice_intent",
                max_tokens=500,
                temperature=0.2,
            )
        except Exception as exc:
            logger.error("Intent router LLM failed: %s", exc)
            return VoiceIntent(
                daemon="hermes",
                action="clarify",
                confidence=0.0,
                raw_transcript=transcript,
                natural_response="I'm having trouble right now. Try again in a moment.",
            )

        return self._parse_intent(response, transcript)

    def _parse_intent(self, response: str, transcript: str) -> VoiceIntent:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            return VoiceIntent(
                daemon="hermes",
                action="clarify",
                confidence=0.2,
                raw_transcript=transcript,
                natural_response="I'm not sure what you'd like me to do. Could you rephrase?",
            )
        try:
            data = json.loads(match.group(0))
            return VoiceIntent(
                daemon=data.get("daemon", "hermes"),
                action=data.get("action", "clarify"),
                params=data.get("params", {}),
                confidence=float(data.get("confidence", 0.5)),
                raw_transcript=transcript,
                needs_confirmation=bool(data.get("needs_confirmation", False)),
                natural_response=data.get("natural_response", ""),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Intent JSON parse failed: %s", exc)
            return VoiceIntent(
                daemon="hermes",
                action="clarify",
                confidence=0.1,
                raw_transcript=transcript,
                natural_response="I had trouble understanding. Please try again.",
            )


_default_router: VoiceIntentRouter | None = None


async def route_voice_intent(transcript: str, llm_client: Any | None = None) -> VoiceIntent:
    global _default_router
    if _default_router is None:
        if llm_client is None:
            from shared.llm_client import LLMClient
            llm_client = LLMClient()
        _default_router = VoiceIntentRouter(llm_client)
    return await _default_router.route(transcript)


__all__ = ["VoiceIntent", "VoiceIntentRouter", "route_voice_intent"]
