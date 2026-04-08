"""Phase 3 Wave 4: Hermes voice session orchestration.

Wires shared.voice.parakeet_client + kokoro_client + intent_router into a
voice-driven interaction loop for the Jarvis war room. Dead code path until
operator starts Parakeet (ASR) and Kokoro (TTS) services on the Studio —
Phase 42.5 v2 operator checklist item.

Flow:
1. Audio input  -> Parakeet (ASR)        -> text
2. Text         -> intent_router         -> daemon dispatch (titan/clawdbot/...)
3. Response     -> Kokoro (TTS)          -> audio output

Opt-in via ENABLE_VOICE_LOOP=true. All imports of shared.voice.* are LAZY
(inside the function body) so this module loads cleanly on a machine that
has no Parakeet/Kokoro services installed — which is the default state
until the operator runs the Phase 42.5 v2 install checklist.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.hermes.voice")


def voice_loop_enabled() -> bool:
    """Runtime flag check.

    Returns True only when the operator has explicitly opted in via
    ENABLE_VOICE_LOOP=true. Liveness of Parakeet/Kokoro services is
    checked separately inside :func:`handle_voice_input`.
    """
    return os.environ.get("ENABLE_VOICE_LOOP", "false").lower() == "true"


async def handle_voice_input(
    audio_bytes: bytes,
    *,
    llm_client: Any,
) -> dict:
    """Process an audio input through the full voice loop.

    Status values:
      * ``"skipped"``  — flag is OFF, services unreachable, or modules missing
      * ``"complete"`` — full ASR -> intent -> TTS round trip succeeded
      * ``"failed"``   — intermediate error (empty transcript, router error,
                        TTS error, etc.)

    Returns:
        dict with keys: status, reason (when skipped), transcription,
        response_text, response_audio, daemon_dispatched, error.
    """
    if not voice_loop_enabled():
        return {"status": "skipped", "reason": "ENABLE_VOICE_LOOP=false"}

    # LAZY imports — keeps module importable even when shared.voice deps
    # (httpx, parakeet daemon, kokoro daemon) are not installed/running.
    try:
        from shared.voice.parakeet_client import ParakeetClient
        from shared.voice.kokoro_client import KokoroClient
        from shared.voice.intent_router import route_voice_intent
    except ImportError as exc:
        logger.debug("Voice loop modules not fully wired: %s", exc)
        return {
            "status": "skipped",
            "reason": f"Voice modules not available: {exc}",
        }

    try:
        asr = ParakeetClient()
        tts = KokoroClient()

        # Liveness check — if either daemon is down, fall through cleanly.
        # Both clients expose async health_check() returning bool.
        try:
            asr_ok = await asr.health_check()
            tts_ok = await tts.health_check()
        except Exception as exc:  # network/timeouts → treat as unreachable
            logger.debug("Voice service health check raised: %s", exc)
            return {
                "status": "skipped",
                "reason": f"Voice service health check failed: {exc}",
            }

        if not asr_ok or not tts_ok:
            return {
                "status": "skipped",
                "reason": "Parakeet or Kokoro service not reachable",
            }

        # 1. Transcribe audio
        transcription_result = await asr.transcribe(audio_bytes)
        transcription = getattr(transcription_result, "text", "") or ""
        if not transcription.strip():
            return {
                "status": "failed",
                "transcription": "",
                "error": "Empty transcription from Parakeet",
            }

        # 2. Route intent (returns a VoiceIntent dataclass)
        intent = await route_voice_intent(transcription, llm_client=llm_client)
        daemon = getattr(intent, "daemon", "hermes")
        response_text = getattr(intent, "natural_response", "") or ""

        if not response_text:
            return {
                "status": "failed",
                "transcription": transcription,
                "daemon_dispatched": daemon,
                "error": "Intent router returned empty natural_response",
            }

        # 3. Synthesize spoken response
        speech_result = await tts.synthesize(response_text)
        response_audio = getattr(speech_result, "audio_bytes", b"")

        return {
            "status": "complete",
            "transcription": transcription,
            "response_text": response_text,
            "response_audio": response_audio,
            "daemon_dispatched": daemon,
            "error": None,
        }

    except Exception as exc:
        logger.exception("Voice loop exception: %s", exc)
        return {"status": "failed", "error": str(exc)}


__all__ = ["voice_loop_enabled", "handle_voice_input"]
