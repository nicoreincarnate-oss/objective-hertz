"""Kokoro TTS client (82M params, sub-real-time on CPU).

Apache 2.0 model. Runs on Apple Silicon at sub-real-time even without GPU
acceleration. Outputs 24kHz PCM audio.

Daemon runs as a separate process on the Mac Studio:
  /opt/perseus/runtime/kokoro_daemon/
  bind: 127.0.0.1:11441 (HTTP API, OpenAI-compatible /v1/audio/speech)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("perseus.voice.kokoro")


@dataclass
class SpeechResult:
    audio_bytes: bytes
    sample_rate: int = 24_000
    duration_s: float = 0.0
    duration_inference_ms: int = 0
    voice: str = "default"
    model: str = "kokoro-82m"


class KokoroClient:
    """HTTP client for the Kokoro TTS daemon."""

    def __init__(
        self,
        api_base: str | None = None,
        timeout: float = 30.0,
        default_voice: str = "af_bella",  # Kokoro female voice
    ):
        self.api_base = api_base or os.environ.get(
            "KOKORO_API_BASE", "http://127.0.0.1:11441/v1"
        )
        self.timeout = timeout
        self.default_voice = default_voice

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        format: str = "wav",
    ) -> SpeechResult:
        """Send text to Kokoro daemon, return audio bytes."""
        import time
        import httpx
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.api_base}/audio/speech",
                json={
                    "model": "kokoro-82m",
                    "input": text,
                    "voice": voice or self.default_voice,
                    "speed": speed,
                    "response_format": format,
                },
            )
            response.raise_for_status()
            audio_bytes = response.content
        return SpeechResult(
            audio_bytes=audio_bytes,
            duration_inference_ms=int((time.perf_counter() - t0) * 1000),
            voice=voice or self.default_voice,
        )

    async def health_check(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.api_base.rsplit('/v1', 1)[0]}/healthz")
                return response.status_code == 200
        except Exception:
            return False


_default_client: KokoroClient | None = None


def _get_default_client() -> KokoroClient:
    global _default_client
    if _default_client is None:
        _default_client = KokoroClient()
    return _default_client


async def synthesize_speech(text: str, **kwargs: Any) -> SpeechResult:
    return await _get_default_client().synthesize(text, **kwargs)


__all__ = ["KokoroClient", "SpeechResult", "synthesize_speech"]
