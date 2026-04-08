"""Voice loop for Hermes/Jarvis war room.

Pipeline:
  microphone → Parakeet v3 (Apple Neural Engine) → text
  text → Qwen3-30B-A3B local → response
  response → Kokoro 82M → audio out

End-to-end latency target: <2 seconds, fully offline.
"""

from shared.voice.parakeet_client import ParakeetClient, transcribe_audio
from shared.voice.kokoro_client import KokoroClient, synthesize_speech
from shared.voice.intent_router import VoiceIntentRouter, route_voice_intent

__all__ = [
    "ParakeetClient",
    "transcribe_audio",
    "KokoroClient",
    "synthesize_speech",
    "VoiceIntentRouter",
    "route_voice_intent",
]
