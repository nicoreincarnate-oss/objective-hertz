# Phase 42.5 Cutover Playbook

The risk-front-loaded daemon migration order, with explicit gates between
each step. Use this when executing Plan 42-5-06.

## Prerequisites (must all be true)

- [ ] All Phase 40-44 implementation merged
- [ ] All Plan 42-5-01 spike reports GREEN or YELLOW (license memo NOT required — operator decision 2026-04-07)
- [ ] Plan 42-5-02 sandbox profile passes red-team tests
- [ ] Plan 42-5-03 Studio provisioned, all models downloaded, /healthz green
- [ ] Plan 42-5-04 verifier code merged, Aider loops working in dev
- [ ] Plan 42-5-05 shadow mode 72h complete, agreement rate ≥88% per daemon
- [ ] Rollback runbook drilled and timed in <60 minutes
- [ ] 2TB Samsung T9 NVMe installed
- [ ] `iogpu.wired_limit_mb=28672` set and persisted
- [ ] Hermes Telegram alerts wired and tested

## Cutover order

### Day 1: Perseus (lowest blast radius)

**Goal**: warm up the local tier infrastructure on the safest daemon. If
anything's broken in the routing or verifier, we find out here without losing
revenue or trust.

**Routes**:
- Log triage → `local-mlx/cheap` (Qwen3-8B)
- Health narratives → `local-mlx/fast` (Qwen3-30B-A3B)
- Cron diff explanations → `local-mlx/cheap`
- Self-audit file analysis → STAYS on cloud SMART (Sonnet)
- Self-audit code edit generation → STAYS on cloud SMART

**How**:
```bash
# 1. Update routing_policy.yaml
sed -i '' 's/perseus_routing: cloud/perseus_routing: local/' config/routing_policy.yaml

# 2. Restart Perseus only
make stop-perseus && make start-perseus

# 3. Watch for 30 minutes
tail -f logs/perseus.log
psql perseus -c "SELECT tier, COUNT(*), AVG(latency_ms) FROM tier_spend_log WHERE daemon='perseus' AND timestamp > NOW() - INTERVAL '30 minutes' GROUP BY tier;"
```

**Pass criteria**:
- ≥95% of Perseus calls served by `local-mlx/*`
- p95 latency <3s
- Zero verifier L3 failures
- No memory pressure warnings

**Rollback if**: any criteria fails. Run `python -m scripts.rollback_litellm --soft --reason "perseus cutover failed"`.

**Soak**: 24 hours

---

### Day 2: Hermes (low stakes, high volume, voice loop activation)

**Goal**: validate the chat tier + activate voice loop. Hermes has high call
volume but the calls are not customer-facing.

**Routes**:
- Telegram chat replies → `local-mlx/fast`
- Daily briefings → `local-mlx/fast`
- Alert classification → `local-mlx/cheap`
- Sev1 incident narratives → STAYS on cloud SMART
- Voice intent routing → `local-mlx/fast` (Qwen3-30B-A3B)
- ASR → Parakeet local
- TTS → Kokoro local

**How**:
```bash
# 1. Activate voice loop
sudo launchctl load /Library/LaunchDaemons/com.perseus.parakeet.plist
sudo launchctl load /Library/LaunchDaemons/com.perseus.kokoro.plist

# 2. Verify voice servers
curl http://127.0.0.1:11440/healthz
curl http://127.0.0.1:11441/healthz

# 3. Update routing_policy
sed -i '' 's/hermes_routing: cloud/hermes_routing: local/' config/routing_policy.yaml

# 4. Restart Hermes
make stop-hermes && make start-hermes

# 5. Test voice loop end-to-end
python -m hermes.jarvis.voice_session --test-mode
```

**Pass criteria**:
- Telegram replies feel right (subjective check by operator)
- Briefings render fully with all fields populated
- Voice loop end-to-end <2s
- Voice intent confidence ≥0.7 average

**Soak**: 24 hours

---

### Day 3-5: Ruflo (HIGHEST RISK — 48h soak + hand-review)

**Goal**: validate Aider loop + sandbox + first 50 patches reviewed by hand.

**Routes**:
- Bug triage → `local-mlx/structured` (grammar-constrained)
- Error classification → `local-mlx/cheap`
- Aider Architect → `cloud/smart` (Sonnet 4.6 or local-heavy if AirLLM is GREEN)
- Aider Editor → `local-mlx/coder` (Qwen2.5-Coder-14B)
- Verifier → pytest in macOS sandbox
- Cross-file refactors >50 LOC → STAYS on cloud SMART

**How**:
```bash
# 1. Enable Aider mode
sed -i '' 's/RUFLO_AIDER_ENABLED=false/RUFLO_AIDER_ENABLED=true/' .env

# 2. Enable hand-review queue (NOT autonomous merge yet)
sed -i '' 's/RUFLO_AUTO_APPLY_FIXES=true/RUFLO_AUTO_APPLY_FIXES=false/' .env

# 3. Restart Ruflo
make stop-ruflo && make start-ruflo

# 4. Wait for first patches to flow into review queue
psql perseus -c "SELECT COUNT(*) FROM ruflo_patterns WHERE created_at > NOW() - INTERVAL '4 hours';"

# 5. HAND-REVIEW first 50 patches via the operator UI at:
#    http://localhost:8500/ruflo/review-queue
#
# For each patch:
# - Read the architect plan
# - Read the editor diff
# - Run the new test locally if needed
# - Approve or reject
# - Note any pattern: "good", "bad", "unclear"
```

**Pass criteria**:
- ≥80% of first 50 patches approved by hand-review
- ≥0 patches caused production breakage when applied
- Aider loop iteration count distribution: most fixes in 1-2 iterations
- Sandbox red-team test still passes (re-run if any change)

**After 50 reviewed patches**: enable autonomous merge:
```bash
sed -i '' 's/RUFLO_AUTO_APPLY_FIXES=false/RUFLO_AUTO_APPLY_FIXES=true/' .env
make stop-ruflo && make start-ruflo
```

**Soak**: 48 hours

---

### Day 6: Deerflow (low stakes, internal only)

**Routes**:
- Source scoring → `local-mlx/fast`
- Brief drafts → `local-mlx/fast`
- Daily brief synthesis → `local-mlx/fast`
- Long-context synthesis (>32k) → `cloud/longctx` (Gemini 3.1 Pro) or `local-mlx/longctx-dense` (Gemma 4 31B Dense)
- Final operator-facing briefs → STAYS on cloud SMART
- Document/chart parsing → `cloud/vision` (Kimi K2.5)

**Soak**: 24 hours

---

### Day 7: Openjarvis (DAG labels + light synthesis)

**Routes**:
- DAG node labels → `local-mlx/cheap`
- Telemetry summaries → `local-mlx/fast`
- Workflow synthesis (≤4 steps) → `local-mlx/fast`
- Mega-plans → `local-airllm/heavy-llama` (if AirLLM GREEN) OR `cloud/genius` (Opus 4.6)
- Alpha/Beta arbitration → STAYS on `cloud/genius` (Opus 4.6 always)

**Soak**: 24 hours

---

### Day 8: Conway (ledger explanations only — wallets stay deterministic)

**Routes**:
- Ledger reconciliation explanations → `local-mlx/fast`
- Budget reports → `local-mlx/fast`
- Strategic agent economics decisions → `cloud/genius` (Opus)
- Wallet signing → **NEVER LLM** (deterministic eth_account)

**Soak**: 24 hours

---

### Day 9-10: Titan stages 1-6 (revenue pipeline, customer-adjacent)

**Routes**:
- Stage 1 (lead discovery strategy) → `local-mlx/fast`
- Stage 2 (lead research summaries) → `local-mlx/fast` + semantic cache (cacheable)
- Stage 3 (email composition first draft) → `local-mlx/structured` + verifier (PII linter, brand voice check)
- Stage 4 (email send validation) → `local-mlx/cheap`
- Stage 5 (follow-up reply classification + generation) → `local-mlx/fast`
- Stage 6 (sync analytics) → `local-mlx/cheap`
- Stages 7-10 (final outbound, pricing, negotiation, invoice) → STAYS on cloud SMART
- Neuro-scorer activation → `local-mlx/vision-day` if vision needed

**Pass criteria**:
- Brand voice linter rejection rate <5%
- Zero PII leaks in outbound drafts
- Open rate within 10% of cloud-only baseline (measured over 48h)

**Soak**: 48 hours

---

### Day 11-12: Clawdbot LAST (highest customer-facing risk)

**Routes**:
- Brain orchestration → `cloud/genius` (Opus stays)
- Section planner → `cloud/smart` (Sonnet stays)
- Aider Architect → `cloud/smart` or `cloud/genius` for premium tier
- Aider Editor → `local-mlx/coder` (Qwen2.5-Coder-14B)
- Visual scorer → `local-mlx/vision-day` (Qwen3-VL-7B) primary, `cloud/vision` (Kimi K2.5) fallback
- Asset generation iteration drafts → Draw Things local
- Asset generation production → Recraft API (per CARL)
- 21st.dev component fetching → existing 21st.dev MCP (per CARL)
- Final HTML/CSS for customer sites → STAYS on `cloud/smart`
- Customer-facing QA narrative → STAYS on `cloud/smart`

**Pass criteria**:
- Visual scorer pass rate ≥85% on first attempt
- No customer reports of broken pages in 48h
- Build cost per site within target

**Soak**: 48 hours

---

## Post-cutover

Once all 8 daemons are on local for 7 stable days:

1. Bake the routing into PROD config (remove the per-daemon flags)
2. Run `/gsd:plan-phase 43` to start the auto-classifier on the now-rich
   escalation log dataset
3. Schedule monthly drill of the rollback runbook
4. Set up a weekly retrospective meeting (just operator) to review escalation
   patterns + tier downgrades

## Daily review during cutover

Each day, before bumping to the next daemon:

```bash
make health
python -m scripts.show_spend --by-daemon
python -m scripts.show_spend --escalations
python -m scripts.show_spend --local-share
psql perseus -c "SELECT * FROM escalation_patterns ORDER BY occurrences DESC LIMIT 10;"
```

Look for:
- Local share trending up (>70% target)
- Escalation rate trending down (<25% target)
- No daemon below 75% task success
- No memory pressure warnings
- Cost trending toward $200-350/mo target
