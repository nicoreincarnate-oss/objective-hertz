## 🚫 PRE-LAUNCH AUDIT COMPLETE — 2026-04-07 (read this FIRST)

**Verdict**: BLOCK LAUNCH
**Critical findings (P0)**: 12 (4 confirmed by 5+ independent audits)
**Major findings (P1)**: 16
**Read first**: `docs/audits/pre-launch/SUMMARY.md`

### The big finding (you would not have caught this without the audit)

Main's `shared/llm_client.py` is **1711 lines** and ALREADY HAS tier routing, fallback chains, StickyLatch caching, budget gating with downgrade, watchdog timeouts per tier, and AirLLM heavy local integration. The worktree branched off a 257-line stale version and built a parallel tier system as if main didn't already have one.

**Phase 42.5 v2 is largely a duplicate of work that already exists in main**, structured differently. The 50 new files I wrote are partially redundant. They are also completely disconnected from production — `grep -r "from shared.tiers" perseus/ titan/ hermes/ clawdbot/ conway/ deerflow_research/ ruflo/ openjarvis/` returns ZERO matches.

The cutover playbook's "FLIP" step has nothing to flip. Setting `LITELLM_PROXY_ENABLED=true` and restarting daemons would do literally nothing.

### Other top P0s

1. **Verifier sandbox is decorative** — tilde paths don't expand on macOS sandbox-exec, no Python wrapper invokes sandbox-exec, Ruflo Aider has `sandbox_runner=None` default. Prompt-injected fix → arbitrary code in host process → SSH/wallet/env exfiltration.
2. **Unsalted SHA-256 KDF** in escalation log encryption — GPU-crackable in hours.
3. **Crypto fail-OPEN** — escalation log writes plaintext when env var unset.
4. **Self-consistency vote launders failures into successes** — when both retry samples fail, `1/1 = 1.0 ≥ 2/3` evaluates True.
5. **Grammar compiler silently degrades to "string"** for `$ref`, `$defs`, `anyOf`, `allOf` — the "grammar-constrained" guarantee is vacuous on real schemas.
6. **15 env vars missing from `.env.example`**, 4 launchd plists missing, 6 of 8 daemons have no individual healthcheck, 140+ outstanding UAT items across 10 phases.

### What's good

- Code quality: 8.83/10 average, zero LLM slop, 26/26 files AST-clean (`/quality-gate`)
- Spec adherence: 98% (`/misalignment-detector`)
- All locked operator decisions correctly reflected (Qwen3-30B-A3B, no licensing, voice replaces ElevenLabs, native services Option B, AirLLM defer, etc.)
- Documentation is operator-grade (6 runbooks, all cross-referenced)
- Strong primitives (`shared/tiers.py`, redactor, semantic cache safety, GBNF spike script) that can be ported to main

### Recommended next action

**Tomorrow morning (or whenever you wake up):**

1. Read `docs/audits/pre-launch/SUMMARY.md` (15 min)
2. Read `docs/audits/pre-launch/07-paul-audit.md` (most thorough — 10 min)
3. Read `docs/audits/pre-launch/15-gsd-review.md` (most surprising new findings — 5 min)
4. Decide: rebase + port modules to main (~7 days with two engineers, ~14 days solo) OR rewrite Phase 42.5 from a fresh branch off main (~3 weeks). Recovery plan in SUMMARY.md.
5. When ready: tell me to start "Day 1 quick wins" — I'll clear the 8 quick-win P0s in parallel (~7-8 hours of work).

### Recovery is bounded

~14 working days solo, ~7 with two engineers. Fits in your 6-8 week launch window. The audit pass paid for itself in the first hour.

---

# Phase 42.5 Autonomous Build — Handoff (UPDATED 2026-04-07 evening)

**Date**: 2026-04-07
**Worktree**: `.claude/worktrees/charming-elion`
**Branch**: `claude/charming-elion`
**Latest commit**: see `git log --oneline -3`

## What changed in this session (after the first autonomous run)

The operator answered key questions and locked 4 more decisions:

1. **Voice loop = full local** — Parakeet + Kokoro replace ElevenLabs entirely. Saves $50-200/mo.
2. **Native macOS services (Option B)** — Postgres/Qdrant/Mem0/N8N/Redis run as launchd services, not Docker. Saves ~2 GB Docker VM overhead.
3. **1TB USB-C drive (1000 MB/s) is the interim external storage** — operator already owns it. Samsung T9 2TB Thunderbolt deferred 1-2 weeks for budget.
4. **AirLLM heavy tier DEFERRED until T9 arrives** — 1000 MB/s drive too slow for Llama 70B streaming (~1-2 tok/s). Until T9, all heavy thinking escalates to cloud Opus 4.6.
5. **Cutover approach changed**: was day-by-day (12 days), now all-at-once. Operator confirmed Perseus has no production traffic, no customers, so risk-front-loaded pacing is overkill. New flow: BUILD → DRY-RUN → SHADOW (synthetic) → FLIP → ITERATE.
6. **Clawdbot** confirmed (operator briefly typed "OpenClaw (CloudBot)" — meant Clawdbot).
7. **OpenRouter + Recraft API keys**: operator doesn't have them yet. Will set up tomorrow or in coming days. Until then, LiteLLM proxy uses direct Anthropic + Ollama only. Clawdbot uses Draw Things only.
8. **Conway keystore password**: doesn't exist yet. Set up during Studio provisioning (~2 min).

---

## Memory budget — UPDATED for "everything on Studio" + native services

| Component | RAM |
|---|---|
| macOS | 4 GB |
| Native services (Postgres + Qdrant + Mem0 + N8N + Redis as launchd, NOT Docker) | ~1.5 GB |
| 8 Perseus daemons | ~8 GB |
| MLX hot set (Qwen3-30B-A3B + embeddings/reranker + Parakeet + Kokoro) | ~22 GB |
| Headroom for KV cache + cold-loaded models on demand | ~0.5 GB |
| **Total** | **~36 GB** ✓ Just fits |

`iogpu.wired_limit_mb=22000` (down from original 28672 to fit everything colocated).

**What's NOT in the always-resident hot set anymore** (swap-on-demand only):
- Qwen3-8B (was always-resident in v1, now swaps in when local-mlx/cheap is needed)
- Qwen2.5-Coder-14B (always was swap-on-demand)
- Qwen3-VL-7B and Qwen3-VL-32B (swap-on-demand)
- Llama 3.3 70B / Qwen 72B via AirLLM (DEFERRED entirely until T9 NVMe)

---

## What I wrote in this session

### NEW
- `docs/runbooks/native-services-setup.md` — step-by-step Postgres/Qdrant/Mem0/N8N/Redis setup as native macOS launchd services. Replaces Docker. ~2 hour first-time setup.

### REWRITTEN
- `docs/runbooks/cutover-playbook.md` — leads with all-at-once 5-step flow (BUILD → DRY-RUN → SHADOW → FLIP → ITERATE), demotes day-by-day to fallback. Reflects operator's pre-launch situation.
- `docs/runbooks/voice-loop-guide.md` — adds the "replaces ElevenLabs" header, F5-TTS upgrade path note for voice cloning later.

### UPDATED
- `shared/tiers.py` — `LOCAL_HEAVY` tier marked as DEFERRED until T9 NVMe, fallback chain updated to route LOCAL_HEAVY → GENIUS (cloud Opus) until then.
- `~/.claude/projects/.../memory/project_local_tier_phase_42_5_v2.md` — memory budget revised, native services section added, AirLLM deferred section, voice-loop-replaces-ElevenLabs section.
- `.paul/STATE.md` — 4 new decisions appended.

### Existing files from first session (still valid)
- All 40 files from commit `30770c0` — `shared/tiers.py`, `shared/semantic_cache.py`, `shared/tier_classifier.py`, `shared/lead_worker.py`, `shared/aider/*`, `shared/voice/*`, `shared/imagegen/*`, `shared/verifier/*`, `shared/escalation_log/*`, `shared/spend_alerts.py`, `config/litellm_config.yaml`, `config/langfuse_evals.yaml`, all migrations, all spike scripts, all tests, all 5 runbooks.

---

## Studio provisioning — Day 1 plan

Tomorrow, when you're on the Studio:

```bash
# 1. macOS hardening (5 min)
sudo sysctl iogpu.wired_limit_mb=22000
echo "iogpu.wired_limit_mb=22000" | sudo tee -a /etc/sysctl.conf
# Disable Spotlight on /Volumes/perseus-models and /opt/perseus/models

# 2. Plug in + format the 1TB USB-C drive (5 min)
# Disk Utility → Erase → APFS, name "perseus-models"

# 3. Native services setup (~2 hours, follow native-services-setup.md)
# - Disable Docker Desktop
# - brew install postgresql@16 + start
# - createdb perseus + run all migrations
# - brew install qdrant + start
# - brew install redis + start
# - pip install mem0ai + launchd plist
# - npm install -g n8n + launchd plist

# 4. Conway setup (~5 min)
openssl rand -base64 32 > ~/.perseus_secrets/conway_password
echo "CONWAY_KEYSTORE_PASSWORD=$(cat ~/.perseus_secrets/conway_password)" >> .env

# 5. Install Ollama + pull hot models (~30 min)
brew install ollama
brew services start ollama
ollama pull qwen3:30b-a3b-mlx-4bit  # ~17 GB
ollama pull dengcao/Qwen3-Embedding-0.6B:f16
ollama pull dengcao/Qwen3-Reranker-0.6B:f16
# Cold models go to /Volumes/perseus-models

# 6. Install voice daemons (~15 min)
brew install macparakeet  # or FluidAudio install method
sudo launchctl load /Library/LaunchDaemons/com.perseus.parakeet.plist
pip install kokoro-onnx
sudo launchctl load /Library/LaunchDaemons/com.perseus.kokoro.plist

# 7. Run spikes (~30 min)
python -m scripts.spikes.run_gbnf_spike --schema ruflo --num-prompts 100
python -m scripts.spikes.run_litellm_hook_spike
# (Skip AirLLM spike — deferred until T9)

# 8. Run sandbox red-team test (~10 min)
# (Test file needs to be written tomorrow — see deferred items)

# 9. Run regression tests against the new stack
REGRESSION_BACKEND=litellm pytest tests/test_phase44_regression.py -v
```

Total Day 1 time: ~4-5 hours of focused work.

If everything is green at the end of Day 1, you can run the FLIP step (Step 4 of cutover playbook) on Day 1 evening or Day 2.

---

## Things still on the operator action list

### When you have budget (next 1-2 weeks)
- [ ] Order **Samsung T9 2TB Thunderbolt NVMe** (~$240)
- [ ] Set up **Recraft API key** (Clawdbot production assets)
- [ ] Set up **OpenRouter API key** (Kimi K2.5, MiMo-V2-Pro, DeepSeek V4, Gemini 3.1 Pro routing)

When all 3 are done, we can:
1. Enable AirLLM heavy tier for Llama 70B / Qwen 72B local heavy thinking
2. Switch Clawdbot production assets from Draw Things → Recraft
3. Enable cloud tier escalation through OpenRouter (currently direct Anthropic only)

### Tonight (operator: pick one)

I keep offering A/B/C and you keep answering questions instead (totally fine). Tonight I'm going to:

1. **Save state and stop** — everything is committed. You sleep, wake up, do Studio Day 1.

That's the only sensible thing left at this hour. Anything else (writing the Ruflo sandbox subprocess wrapper, testing the spike scripts) needs the Studio physically present, and that's tomorrow.

---

## Resume instructions for tomorrow

1. SSH or sit at the Mac Studio
2. `git pull` on the main branch (after I push the worktree)
3. `cat .paul/HANDOFF.md` (this file)
4. `cat docs/runbooks/native-services-setup.md` and follow it (~2 hours)
5. `cat docs/runbooks/cutover-playbook.md` Step 1 (BUILD) and follow it (~6 hours)
6. Spike runs after Step 1 complete (~30 min)
7. SHADOW + FLIP whenever you're ready (could be Day 1 evening or Day 2)

---

## Cost projection — UPDATED for AirLLM defer + missing keys

Without AirLLM heavy tier and without OpenRouter (so all cloud routing goes direct to Anthropic):

| Path | Calls/day | Avg cost/call | Daily | Monthly |
|---|---|---|---|---|
| Local tier (Qwen3-30B-A3B + cheap + structured) | ~700 | $0.00 | $0.00 | $0.00 |
| Smart escalation (Sonnet 4.6 direct) | ~150 | $0.04 | $6.00 | $180 |
| Heavy escalation (Opus 4.6 direct, replaces deferred AirLLM) | ~30 | $0.50 | $15.00 | $450 |
| Vision (Sonnet vision until OpenRouter+Kimi available) | ~20 | $0.08 | $1.60 | $48 |
| Voice loop (Parakeet+Kokoro local) | ~50 | $0.00 | $0.00 | $0.00 |
| Image gen (Draw Things local until Recraft) | ~10 | $0.00 | $0.00 | $0.00 |
| **Total** | **~960** | | **~$22.60** | **~$680** |

That's HIGHER than the $200-350/mo target because of the AirLLM defer. Heavy thinking on cloud Opus is expensive.

**When T9 arrives and AirLLM is enabled**: Opus calls drop ~80% (only the hardest 5% stay on cloud). New monthly: ~$680 - $360 (Opus savings) + $20 (electricity) = **~$340/mo**.

**When OpenRouter is set up and Kimi K2.5 is the vision tier**: vision savings ~$30/mo. New monthly: ~$310/mo. ✓ Within target.

So the timeline looks like:
- Week 1 (now → ~Day 14): ~$680/mo while AirLLM is deferred and OpenRouter is missing
- Week 2-3 (T9 arrives, OpenRouter set up): ~$310/mo
- Week 4+: target met

Operator approved "quality > cost" so this is fine. Just being honest about the trajectory.

---

## What I want from you tomorrow morning

1. **Read this file** (5 min)
2. **Read `docs/runbooks/native-services-setup.md`** (5 min) — understand what's about to happen on the Studio
3. **Read `docs/runbooks/cutover-playbook.md` Steps 1-5** (10 min) — the all-at-once flow
4. **Start Studio Day 1** following native-services-setup → cutover Step 1

Total reading: ~20 minutes. Total Day 1 doing: ~5 hours.

If you hit any blocker during Studio Day 1, ping me with what's failing and I'll debug it.

— Claude
