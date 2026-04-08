"""Parakeet v3 ASR client (FluidAudio Core ML on Apple Neural Engine).

Uses NVIDIA Parakeet TDT v3 model packaged for macOS via FluidAudio's MacParakeet.
~66 MB working set, runs on Apple Neural Engine (zero GPU usage), sub-real-time.

Best WER on English among small models. Falls back to Whisper Large v3 Turbo if
Parakeet daemon is unavailable.

Daemon runs as a separate process on the Mac Studio:
  /opt/perseus/runtime/parakeet_daemon/
  bind: 127.0.0.1:11440 (HTTP API, OpenAI-compatible /v1/audio/transcriptions)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.voice.parakeet")


@dataclass
class TranscriptionResult:
    text: str
    confidence: float = 0.0
    duration_audio_s: float = 0.0
    duration_inference_ms: int = 0
    model: str = "parakeet-v3"
    language: str = "en"


class ParakeetClient:
    """HTTP client for the Parakeet daemon."""

    def __init__(
        self,
        api_base: str | None = None,
        timeout: float = 10.0,
        fallback_to_whisper: bool = True,
    ):
        self.api_base = api_base or os.environ.get(
            "PARAKEET_API_BASE", "http://127.0.0.1:11440/v1"
        )
        self.timeout = timeout
        self.fallback_to_whisper = fallback_to_whisper

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int = 16_000,
        format: str = "wav",
    ) -> TranscriptionResult:
        """Send audio to Parakeet daemon, return transcription.

        Falls back to whisper.cpp if Parakeet daemon is down and fallback is enabled.
        """
        try:
            return await self._transcribe_via_parakeet(audio_bytes, format=format)
        except Exception as exc:
            logger.warning("Parakeet transcription failed: %s", exc)
            if self.fallback_to_whisper:
                return await self._fallback_whisper(audio_bytes)
            raise

    async def _transcribe_via_parakeet(self, audio_bytes: bytes, format: str = "wav") -> TranscriptionResult:
        import time
        import httpx
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            files = {"file": (f"audio.{format}", audio_bytes, f"audio/{format}")}
            data = {"model": "parakeet-v3"}
            response = await client.post(
                f"{self.api_base}/audio/transcriptions",
                files=files,
                data=data,
            )
            response.raise_for_status()
            result = response.json()
        return TranscriptionResult(
            text=result.get("text", "").strip(),
            confidence=result.get("confidence", 0.0),
            duration_audio_s=result.get("duration", 0.0),
            duration_inference_ms=int((time.perf_counter() - t0) * 1000),
            model="parakeet-v3",
        )

    async def _fallback_whisper(self, audio_bytes: bytes) -> TranscriptionResult:
        """Fallback to whisper.cpp via local subprocess if available."""
        import time
        import subprocess
        import tempfile
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            audio_path = Path(f.name)
        try:
            result = subprocess.run(
                ["whisper-cli", "-f", str(audio_path), "-m", "large-v3-turbo", "-otxt", "-of", "-"],
                capture_output=True, text=True, timeout=30,
            )
            text = result.stdout.strip()
        except Exception as exc:
            logger.error("Whisper fallback failed: %s", exc)
            text = ""
        finally:
            audio_path.unlink(missing_ok=True)
        return TranscriptionResult(
            text=text,
            confidence=0.5,
            duration_inference_ms=int((time.perf_counter() - t0) * 1000),
            model="whisper-large-v3-turbo-fallback",
        )

    async def health_check(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.api_base.rsplit('/v1', 1)[0]}/healthz")
                return response.status_code == 200
        except Exception:
            return False


# Module-level convenience function
_default_client: ParakeetClient | None = None


def _get_default_client() -> ParakeetClient:
    global _default_client
    if _default_client is None:
        _default_client = ParakeetClient()
    return _default_client


async def transcribe_audio(audio_bytes: bytes, **kwargs: Any) -> TranscriptionResult:
    return await _get_default_client().transcribe(audio_bytes, **kwargs)


__all__ = ["ParakeetClient", "TranscriptionResult", "transcribe_audio"]
