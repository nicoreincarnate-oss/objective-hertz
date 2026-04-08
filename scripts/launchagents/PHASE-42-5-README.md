# Phase 42.5 v2 — Native Services + Voice Loop LaunchDaemons

These 4 plists are for Phase 42.5 v2 native macOS services that replace Docker containers, plus the voice loop (ASR + TTS) for the Mac Studio.

## Plists

| Plist | Purpose | Port |
|-------|---------|------|
| `com.perseus.parakeet.plist` | FluidAudio MacParakeet ASR daemon (voice-in) | 11440 |
| `com.perseus.kokoro.plist` | Kokoro TTS server (voice-out) | 11441 |
| `com.perseus.mem0.plist` | Mem0 native memory server (replaces Docker) | 8888 |
| `com.perseus.n8n.plist` | N8N workflow automation (native Node) | 5678 |

## Install

```bash
sudo cp scripts/launchagents/com.perseus.{parakeet,kokoro,mem0,n8n}.plist /Library/LaunchDaemons/
```

## Load

```bash
sudo launchctl load /Library/LaunchDaemons/com.perseus.{parakeet,kokoro,mem0,n8n}.plist
```

## Verify

```bash
launchctl list | grep com.perseus
```

## Log locations

All services log to `/Users/majovega/Library/Logs/perseus/`:

- `parakeet.log` / `parakeet.error.log`
- `kokoro.log` / `kokoro.error.log`
- `mem0.log` / `mem0.error.log`
- `n8n.log` / `n8n.error.log`

Ensure the log directory exists before loading:

```bash
mkdir -p /Users/majovega/Library/Logs/perseus
```

## Placeholders — IMPORTANT

**`com.perseus.parakeet.plist`** and **`com.perseus.kokoro.plist`** contain PLACEHOLDER program arguments. They will not run as-is. The actual binaries/modules are installed during **Studio Day 1** as part of the Phase 42.5 v2 cutover:

- **Parakeet**: Install FluidAudio MacParakeet, then update `ProgramArguments` to point to the real `parakeet-server` binary path.
- **Kokoro**: `pip install kokoro` into the Perseus venv, then update the `python3` path to the venv interpreter if different from `/opt/homebrew/bin/python3`.

`com.perseus.mem0.plist` and `com.perseus.n8n.plist` are production-ready assuming the corresponding packages are installed (`pip install mem0ai`, `brew install node@20 && npm i -g n8n`).

## Properties (all 4)

- `KeepAlive=true` — auto-restart on crash
- `RunAtLoad=true` — start on boot
- `ThrottleInterval=10` — min 10s between restarts
- `ExitTimeOut=30` — 30s for graceful shutdown before SIGKILL
- `EnvironmentVariables` — port + path vars

## Audit reference

Added per pre-launch audit finding **P0-8** (2026-04-07): runbooks referenced these plists but the files did not exist in `scripts/launchagents/`, meaning the voice loop and native services would not survive a Mac Studio reboot.
