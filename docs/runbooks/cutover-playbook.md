# Phase 42.5 Cutover Playbook (All-at-Once)

**Updated 2026-04-07**: rewritten for the operator's "do everything now" approach. The original day-by-day pacing was for a system already in production with real customer traffic — that doesn't apply here because Perseus hasn't launched yet. With no customers to break, we BUILD everything, DRY-RUN against synthetic traffic, then FLIP the master switch all at once. The day-by-day playbook is preserved at the bottom as a fallback if the all-at-once flip reveals problems.

---

## Prerequisites (must all be true)

- [ ] All Phase 40-44 implementation merged from `claude/charming-elion` branch
- [ ] Mac Studio M4 Max physically set up with macOS configured
- [ ] 1TB external USB-C drive plugged in, reformatted to APFS, mounted at `/Volumes/perseus-models`
- [ ] Native macOS services running (`docs/runbooks/native-services-setup.md`)
- [ ] `iogpu.wired_limit_mb=22000` set and persisted in `/etc/sysctl.conf`
- [ ] Spotlight indexing disabled on `/Volumes/perseus-models` and `/opt/perseus/models`
- [ ] Time Machine disabled on the Studio
- [ ] Hermes Telegram alerts wired and tested
- [ ] Conway keystore password generated and set in `.env` as `CONWAY_KEYSTORE_PASSWORD`

---

## The 5-step all-at-once cutover

### Step 1: BUILD (Day 1, ~6-8 hours)

Studio provisioning + model downloads + daemon updates.

```bash
# 1.1 Set GPU memory cap
sudo sysctl iogpu.wired_limit_mb=22000
echo "iogpu.wired_limit_mb=22000" | sudo tee -a /etc/sysctl.conf

# 1.2 Install native services (see native-services-setup.md)
# ~2 hours

# 1.3 Install Ollama for MLX serving
brew install ollama
brew services start ollama

# 1.4 Pull all hot models (run in parallel)
ollama pull qwen3:30b-a3b-mlx-4bit              # ~17 GB, ~17 min on 1000 MB/s drive
ollama pull qwen2.5-coder:14b-mlx-4bit          # ~8.5 GB
ollama pull qwen3:8b-mlx-4bit                   # ~4.7 GB
ollama pull qwen2.5vl:7b                        # ~6 GB
ollama pull qwen2.5vl:32b                       # ~18 GB → /Volumes/perseus-models
ollama pull dengcao/Qwen3-Embedding-0.6B:f16    # ~1.2 GB
ollama pull dengcao/Qwen3-Reranker-0.6B:f16     # ~1.2 GB

# 1.5 Install voice daemons
brew tap FluidAudio/parakeet
brew install macparakeet
sudo cp /Users/majovega/Desktop/Projects/objective-hertz/scripts/launchagents/com.perseus.parakeet.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.perseus.parakeet.plist

pip install kokoro-onnx
sudo cp /Users/majovega/Desktop/Projects/objective-hertz/scripts/launchagents/com.perseus.kokoro.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.perseus.kokoro.plist

# 1.6 Install Draw Things app from drawthings.ai
# Open Draw Things → Settings → Network → Enable "Allow Remote Connection"
# Pull SDXL, Flux schnell, Flux dev, Qwen-Image checkpoints from Draw Things model browser

# 1.7 Apply all migrations
for f in scripts/migrations/*.sql; do psql perseus < "$f"; done

# 1.8 Migrate daemon call sites
python -m scripts.migrate_to_litellm --dry-run --report /tmp/migration-preview.json
# Review the JSON
python -m scripts.migrate_to_litellm --apply
git diff   # Verify changes look right
git commit -m "Phase 42.5 cutover: migrate all daemon call sites to tier system"
```

Step 1 complete when:
- `make health` returns all green
- `ollama list` shows all models
- `curl http://127.0.0.1:11440/healthz` returns OK (Parakeet)
- `curl http://127.0.0.1:11441/healthz` returns OK (Kokoro)
- `curl http://127.0.0.1:7860/sdapi/v1/options` returns OK (Draw Things)

---

### Step 2: DRY-RUN spikes (~2 hours)

Verify the foundational assumptions before flipping anything.

```bash
# 2.1 GBNF spike — does grammar-constrained tool calling work?
python -m scripts.spikes.run_gbnf_spike \
    --schema ruflo \
    --num-prompts 100 \
    --port 11434
# Expects GREEN verdict in docs/spikes/gbnf-mlx-vlm-spike.md

# 2.2 LiteLLM hook spike — can we rewrite requests?
python -m scripts.spikes.run_litellm_hook_spike
# Expects GREEN verdict

# 2.3 Sandbox red-team test (CRITICAL)
python -m pytest tests/test_verifier_sandbox_red_team.py -v
# Must pass: no exfiltration, no SSH access, no env var read

# (NOTE: AirLLM spike SKIPPED — deferred until T9 arrives)
```

Step 2 complete when all 3 spikes return GREEN. If any return RED, stop and investigate before proceeding.

---

### Step 3: SHADOW MODE — synthetic traffic (~4 hours)

Run the golden prompt suite through the new tier system. NO production traffic — synthetic only.

```bash
# 3.1 Run golden prompts against the new backend
REGRESSION_BACKEND=litellm pytest tests/test_phase44_regression.py -v

# 3.2 Run the same against the old direct-Anthropic backend for comparison
REGRESSION_BACKEND=anthropic_direct pytest tests/test_phase44_regression.py -v

# 3.3 Diff the results — should be near-identical
# (Failures here mean the new backend is producing materially different output
#  than the old backend on the same prompts. Stop and investigate.)
```

Step 3 complete when the regression suite passes on the new backend with the same characteristics as the old.

---

### Step 4: FLIP the master switch (~5 minutes)

This is the actual cutover. One config change, all daemons go local at once.

```bash
# 4.1 Enable all the flags
sed -i '' 's/LITELLM_PROXY_ENABLED=false/LITELLM_PROXY_ENABLED=true/' .env
sed -i '' 's/LOCAL_TIER_ENABLED=false/LOCAL_TIER_ENABLED=true/' .env
sed -i '' 's/TIERED_ROUTING_ENABLED=false/TIERED_ROUTING_ENABLED=true/' .env
sed -i '' 's/AUTO_TIER_ENABLED=false/AUTO_TIER_ENABLED=true/' .env
sed -i '' 's/LANGFUSE_ENABLED=false/LANGFUSE_ENABLED=true/' .env
sed -i '' 's/SEMANTIC_CACHE_ENABLED=false/SEMANTIC_CACHE_ENABLED=true/' .env
sed -i '' 's/LITELLM_ROLLOUT_PCT=0/LITELLM_ROLLOUT_PCT=100/' .env

# 4.2 Restart all daemons
make restart

# 4.3 Watch the dashboard
open http://localhost:8500
# Or tail the logs
tail -f logs/*.log
```

---

### Step 5: ITERATE (~first 24 hours)

Watch the canary dashboard. Tune routing based on what you see.

```bash
# Check spend distribution
python -m scripts.show_spend --today
python -m scripts.show_spend --by-daemon
python -m scripts.show_spend --by-tier

# Check escalation patterns
python -m scripts.show_spend --escalations

# Check local share per daemon
python -m scripts.show_spend --local-share
```

**Pass criteria** (eyeball after 4 hours):
- Local share ≥70% across all daemons (target: 75%+)
- Zero OOM events
- No `memory_pressure: critical` warnings
- Verifier L3 (external check) pass rate ≥85% per daemon
- p50 latency under 2 seconds for `local-mlx/fast` calls
- Total daily spend tracking to <$15 (~$350/mo annualized)

**If anything looks bad**: run rollback (`docs/runbooks/local-tier-rollback.md`) — flips everything back to cloud in under 1 hour.

---

## What's NOT in this all-at-once cutover (deferred)

### Deferred until T9 NVMe arrives (1-2 weeks)

- **AirLLM heavy tier**: Llama 3.3 70B + Qwen 2.5 72B local heavy thinking. Until T9 arrives, all heavy thinking calls escalate to cloud Opus 4.6.
- **Vision-32B always-resident**: Lives on the 1TB drive, swap-on-demand only.

### Deferred until OpenRouter key set up (operator action — soon)

- Cloud tier routing through OpenRouter (Kimi K2.5, MiMo-V2-Pro, DeepSeek V4, Gemini 3.1 Pro)
- Until OpenRouter is set up, the LiteLLM proxy uses direct Anthropic + Ollama only
- This means cloud escalations go to direct Sonnet 4.6 / Opus 4.6 only
- That's fine — Anthropic models cover the heavy lift

### Deferred until Recraft API key set up (operator action — soon)

- Recraft production-grade asset generation for Clawdbot
- Until Recraft is set up, Clawdbot uses Draw Things for all images
- Quality slightly lower for hero/logo finals but workable

---

## Daily review (first week post-cutover)

```bash
# Each morning
make health
python -m scripts.show_spend --today
python -m scripts.show_spend --by-tier --days 1
python -m scripts.show_spend --escalations
psql perseus -c "SELECT * FROM escalation_patterns ORDER BY occurrences DESC LIMIT 10;"

# Look for:
# - Local share trending up (>70% target)
# - Escalation rate trending down (<25% target)
# - No daemon below 75% task success
# - Cost trending toward $200-350/mo target
```

Tune per-daemon depth caps based on observed patterns. If Ruflo's chains keep escalating at depth 3, raise its cap to 4. If Titan's Aider editor keeps producing bad email drafts, drop its tier from `local` to `agentic`.

---

## FALLBACK: Day-by-day cutover (if all-at-once flip reveals problems)

If Step 4 (the flip) reveals problems that can't be fixed in 24 hours, fall back to per-daemon migration. This isolates which daemon is causing issues.

Risk-front-loaded order:

1. **Day 1: Perseus** — log triage, scheduler narrative, lowest blast radius
2. **Day 2: Hermes** — chat, alerts, voice loop activation
3. **Days 3-5: Ruflo** — 48h soak, hand-review first 50 patches before autonomous merge
4. **Day 6: Deerflow** — internal research only
5. **Day 7: Openjarvis** — DAG labels first, then workflow synthesis
6. **Day 8: Conway** — ledger reconciliation only (wallet signing NEVER LLM)
7. **Days 9-10: Titan stages 1-6** — lead pipeline with PII linter
8. **Days 11-12: Clawdbot** — last because customer-facing risk highest

For each daemon: enable its flag, restart that daemon only, watch for 24 hours, fix any issues before bumping to the next.

This pacing exists if you need it. The all-at-once approach should work for your pre-launch situation — there are no customers to break.
