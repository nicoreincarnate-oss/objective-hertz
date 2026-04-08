# Voice Loop Guide — Hermes/Jarvis War Room

**Operator decision 2026-04-07**: voice loop is FULL LOCAL. Parakeet (ASR) +
Kokoro (TTS) replace ElevenLabs entirely. Saves ~$50-200/mo at moderate use,
keeps audio fully private (no bytes leave the Studio), runs sub-2-seconds
end-to-end.

ElevenLabs Conversational AI integration in Hermes is REMOVED as part of
Phase 42.5 cutover.

The voice loop adds a fully-offline, sub-2-second voice interface to Perseus.
You speak to your Mac Studio, Perseus understands intent, dispatches to the
right daemon, and speaks back.

## Pipeline

```
Mic → Parakeet v3 (ASR, ANE) → text
text → VoiceIntentRouter (Qwen3-30B-A3B local) → daemon + action
daemon executes via existing A2A capability → result
result → Kokoro 82M (TTS) → audio out → speaker
```

End-to-end latency target: <2 seconds for short queries.

## Components

### Parakeet v3 daemon
- Apache 2.0 model from FluidAudio's MacParakeet wrapper
- Runs on Apple Neural Engine (zero GPU usage)
- 66 MB working set
- Sub-real-time on Mac Studio M4 Max
- Best WER on English among small models
- Bind: 127.0.0.1:11440 (HTTP, OpenAI-compatible /v1/audio/transcriptions)
- launchd plist: `com.perseus.parakeet.plist`

### Kokoro TTS daemon
- Apache 2.0 (82M parameter model)
- Runs on CPU at sub-real-time
- Outputs 24kHz PCM
- Bind: 127.0.0.1:11441 (HTTP, OpenAI-compatible /v1/audio/speech)
- launchd plist: `com.perseus.kokoro.plist`
- Default voice: `af_bella` (configurable per session)

### Intent Router
- Lives in `shared/voice/intent_router.py`
- Uses local Qwen3-30B-A3B for intent disambiguation
- Outputs structured JSON: `{daemon, action, params, confidence, natural_response, needs_confirmation}`
- Falls back to "clarify" intent when confidence <0.5

## Code patterns

### From Hermes telegram bot (existing)
```python
from shared.voice import transcribe_audio, route_voice_intent, synthesize_speech

# When user sends a voice note via Telegram
async def on_voice_message(audio_bytes: bytes) -> bytes:
    transcription = await transcribe_audio(audio_bytes)
    intent = await route_voice_intent(transcription.text)

    # Speak back the immediate "got it" response
    immediate_speech = await synthesize_speech(intent.natural_response)
    await send_telegram_audio(immediate_speech.audio_bytes)

    # Then dispatch to daemon
    if intent.needs_confirmation:
        await ask_telegram_confirmation(intent)
        return

    result = await dispatch_to_daemon(intent.daemon, intent.action, intent.params)

    # Speak back the result
    response_speech = await synthesize_speech(format_result_for_voice(result))
    return response_speech.audio_bytes
```

### From Jarvis war room (web socket)
```python
# In hermes/jarvis/voice_session.py (NEW)
async def handle_voice_session(websocket):
    async for audio_chunk in websocket:
        transcription = await transcribe_audio(audio_chunk)
        if transcription.text.strip():
            intent = await route_voice_intent(transcription.text)
            await websocket.send_json({"transcription": transcription.text, "intent": intent.__dict__})

            result = await dispatch_to_daemon(intent.daemon, intent.action, intent.params)
            speech = await synthesize_speech(format_result_for_voice(result))
            await websocket.send_bytes(speech.audio_bytes)
```

## Voice commands the operator can use

| Command | Routes to | Action |
|---|---|---|
| "What's Titan's pipeline today?" | titan | pipeline_status |
| "How many leads did we close?" | titan | deal_count |
| "Pause Titan outreach" (needs confirm) | titan | pause_outreach |
| "Show me today's briefing" | hermes | show_briefing |
| "Any sev1 alerts?" | hermes | alert_summary |
| "What's the wallet balance?" | conway | wallet_balance |
| "How much did we spend this month?" | conway | monthly_spend |
| "Latest research brief" | deerflow | latest_brief |
| "How is Ruflo doing?" | ruflo | fix_status |
| "Restart Hermes" (needs confirm) | perseus | restart_daemon |
| "What are you working on?" | openjarvis | workflow_status |

## Privacy

- ALL audio processing happens on the Mac Studio
- No audio bytes leave the host
- Parakeet, Kokoro, and Qwen3-30B-A3B all run locally
- The transcription text is logged to the operator's local DB only
- Cloud APIs are NEVER called by the voice loop unless intent_router escalates
  via the verifier (which writes to escalation_log with redaction)

## Voice quality trade-off vs ElevenLabs

Kokoro stock voices (`af_bella`, `af_sky`, `af_sarah`, `am_adam`, `am_michael`)
are good but not as expressive as ElevenLabs voice cloning. Reasonable for
operator dialogue and Jarvis war room. If after 1 week of use the stock voices
feel wrong, swap to **F5-TTS** — Apache 2.0, supports voice cloning from a
5-10 second sample, runs natively on Mac. Added to backlog as a post-Phase 42.5
follow-up.

To preview voices before committing:
```python
from shared.voice import synthesize_speech
result = await synthesize_speech("Hello, this is a voice test.", voice="af_bella")
# Save to disk and play
with open("/tmp/voice_test.wav", "wb") as f:
    f.write(result.audio_bytes)
```

## Setup commands

```bash
# 1. Install Parakeet daemon (FluidAudio)
brew tap FluidAudio/parakeet
brew install macparakeet
sudo cp scripts/launchagents/com.perseus.parakeet.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.perseus.parakeet.plist

# 2. Install Kokoro daemon
pip install kokoro-onnx
sudo cp scripts/launchagents/com.perseus.kokoro.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.perseus.kokoro.plist

# 3. Verify
curl http://127.0.0.1:11440/healthz   # Parakeet
curl http://127.0.0.1:11441/healthz   # Kokoro

# 4. Test the loop end-to-end
python -m hermes.jarvis.voice_session --test-mode
```

## Failure modes

| Failure | Symptom | Mitigation |
|---|---|---|
| Parakeet daemon down | Transcription returns empty | Falls back to whisper.cpp via subprocess |
| Kokoro daemon down | TTS returns 503 | Falls back to macOS `say` command (lower quality) |
| Intent router low confidence | Returns "clarify" intent | Operator gets a "I didn't catch that" prompt |
| Daemon dispatch fails | Result is error string | Spoken back as the error message |
