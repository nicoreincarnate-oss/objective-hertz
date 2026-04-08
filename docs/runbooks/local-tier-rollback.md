# Local Tier Rollback Runbook

**Purpose**: Cold-start, single-operator rollback from any local-tier failure to the cloud-only baseline in **≤1 hour**.

**Last drill**: TBD (must be tested in Plan 42-5-05 shadow window)

---

## When to use this runbook

Run the rollback if ANY of these triggers:

| Trigger | Severity | Action |
|---|---|---|
| `local_degraded` Hermes alert (>15% fallback rate over 5 min) | Warning | Soft rollback |
| `memory_pressure: critical` from /healthz | Critical | Hard rollback |
| Daily escalation cost >$30 | Critical | Hard rollback |
| Customer-facing daemon producing visibly wrong output | Critical | Hard rollback + page operator |
| MLX server crash loop (>3 restarts/min) | Critical | Hard rollback |
| `ruflo_patterns` shows pattern of broken patches accepted | Critical | Hard rollback + halt Ruflo |
| Verifier sandbox red-team test fails on a hot-fix change | Critical | Hard rollback + revert change |
| Operator just feels uneasy about the local tier | Any | Soft rollback, investigate |

---

## SOFT rollback (5 minutes, no daemon restart)

Use when you want to flip the local tier off without disrupting in-flight work.

```bash
cd /Users/majovega/Desktop/Projects/objective-hertz
python -m scripts.rollback_litellm --soft --reason "describe reason here"
```

What it does:
1. Sets `LITELLM_PROXY_ENABLED=false`, `LOCAL_TIER_ENABLED=false`, `TIERED_ROUTING_ENABLED=false`, `LITELLM_ROLLOUT_PCT=0` in `.env`
2. Daemons keep running with their current process state
3. New requests go through the legacy direct-Anthropic path
4. MLX servers remain loaded for inspection

After soft rollback:
- Check Hermes War Room for daemon health
- Inspect MLX server logs at `logs/mlx_server_*.log`
- Look at `tier_spend_log` for the pattern that triggered the issue
- Decide: hard rollback, or restart daemons after fixing root cause

---

## HARD rollback (30-60 minutes, full daemon restart)

Use when daemons are in a bad state or you need a clean slate.

### Pre-flight (1 minute)

1. Open a terminal
2. Make sure you can hit Hermes War Room: `curl http://localhost:8500/api/liveness`
3. Have Telegram open in case the operator paging fires

### Step 1: Run the rollback script (5-10 minutes)

```bash
cd /Users/majovega/Desktop/Projects/objective-hertz
python -m scripts.rollback_litellm --reason "describe trigger here"
```

The script does this automatically:
1. Disables 9 feature flags in `.env`
2. Calls `make restart` to restart all daemons
3. Sleeps 10s for daemons to come back
4. Verifies Hermes /healthz responds
5. Pages operator via Telegram

### Step 2: Verify daemons are healthy (5 minutes)

```bash
make health
```

Expected output: All 8 daemons green, all docker services up, Ollama reachable.

If anything is red:
- Check `logs/<daemon>.log` for the most recent stack trace
- If daemon won't start: `make stop && make start`
- If docker is unhappy: `docker compose down && docker compose up -d`

### Step 3: Confirm production traffic is on the old path (5 minutes)

Check the tier_spend_log for the last 5 minutes:

```bash
psql perseus -c "SELECT model_used, COUNT(*) FROM tier_spend_log WHERE timestamp > NOW() - INTERVAL '5 minutes' GROUP BY model_used;"
```

Expected: ALL rows should show direct anthropic/* model IDs (not openrouter, not ollama).

If any local-mlx or openrouter rows appear:
- A daemon hasn't picked up the new env vars
- Restart that daemon individually: `make stop-titan && make start-titan` (substitute daemon)

### Step 4: Notify Telegram channel (2 minutes)

The script auto-pages, but send a manual confirmation:

```
ROLLBACK COMPLETE
Trigger: <reason>
Time: <timestamp>
Cost so far: $X.XX
Status: All daemons green, traffic on direct-Anthropic
Investigation: <link to relevant log/dashboard>
```

### Step 5: Tear down MLX servers if memory needs reclaiming (optional, 2 minutes)

```bash
sudo launchctl unload /Library/LaunchDaemons/com.perseus.mlx-server.plist
sudo launchctl unload /Library/LaunchDaemons/com.perseus.mlx-cheap.plist
```

This frees ~22 GB of unified memory if you need it for diagnostics.

---

## Post-rollback investigation

1. **Capture state**: `make export-logs > /tmp/rollback-investigation-$(date +%s).tar.gz`
2. **Review the trigger**: which alert fired, what was the local response that broke
3. **Read the escalation log**: `psql perseus -c "SELECT * FROM tier_spend_log WHERE escalated_from_tier IS NOT NULL ORDER BY timestamp DESC LIMIT 50;"`
4. **Check verifier patterns**: which Layer (1/2/3/4) was failing most
5. **Open a postmortem doc**: `docs/postmortems/YYYY-MM-DD-local-rollback.md`

## Recovery (when ready to retry)

1. Fix the root cause in code
2. Test the fix in shadow mode against a 24-hour traffic sample
3. Re-enable flags ONE AT A TIME with 6h gaps:
   - First: `LITELLM_PROXY_ENABLED=true` (gateway only, no local)
   - Then: `LOCAL_TIER_ENABLED=true` for ONE daemon (start with Hermes)
   - Then: gradual cutover per Plan 42-5-06 daemon order

---

## Rollback Drill Checklist

Run this drill in Plan 42-5-05 shadow window. Update the timing here when complete.

- [ ] Drill date: _____
- [ ] Cold start to "rollback complete": _____ minutes (target ≤60)
- [ ] All daemons came back green: yes/no
- [ ] Traffic on old path within 10 minutes of script start: yes/no
- [ ] Telegram alert delivered: yes/no
- [ ] Operator could find the postmortem template: yes/no
- [ ] Gaps found: _____
- [ ] Runbook updated based on drill: yes/no
