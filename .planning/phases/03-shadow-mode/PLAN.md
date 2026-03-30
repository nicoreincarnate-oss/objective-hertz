# Phase 3: Shadow Mode Dry Run (Days 14-19)

## Goal

Run the FULL Titan pipeline end-to-end with real data but zero external side effects. No emails leave the system, no sites deploy to Netlify, no invoices go out. Every artifact gets reviewed by Nico in War Room before Phase 4 goes live.

## What Shadow Mode Means

| Stage | Normal Behavior | Shadow Behavior |
|-------|----------------|-----------------|
| Lead Discovery | Scrape real businesses | **Runs normally** (already have 100+ from Phase 2) |
| Lead Research | Firecrawl + LLM summarize | **Runs normally** |
| Email Compose | Claude writes custom emails | **Runs normally** |
| Email Send | Adds leads to Instantly campaign | **BLOCKED** -- logs "SHADOW: would send to {email}" |
| Follow-up | Sends follow-up sequences | **BLOCKED** -- same shadow gate |
| Close Deal | Processes interested leads | **Runs normally** (no real replies in shadow) |
| Build Site | ClawdBot builds 5-page sites | **Builds locally only** -- no Netlify deploy call |
| Deploy Site | Verifies live deployments | **SKIPPED** -- nothing deployed |
| Invoice | Sends Stripe/Wise invoices | **SKIPPED** entirely |
| Analytics Sync | Pulls Instantly campaign stats | **SKIPPED** -- no campaign data |

---

## Task 1: Enable Shadow Mode Flag

### Objective
Add a `shadow_mode` system_config flag that pipeline stages check before performing external actions. This is the kill switch that prevents any outbound communication.

### Files Involved
| File | Action |
|------|--------|
| `shared/db.py` | No changes -- `get_config()` / `set_config()` already support arbitrary keys via `system_config` table |
| `titan/pipeline/email_send.py` | Add shadow mode check before Instantly API calls |
| `titan/pipeline/build_site.py` | Add shadow mode check before `netlify_deploy` call |
| `titan/pipeline/follow_up.py` | Add shadow mode check before follow-up sends |
| `titan/pipeline/invoice.py` | Add shadow mode check to skip invoice creation |
| `titan/pipeline/deploy_site.py` | Add shadow mode check to skip deployment verification |
| `hermes/web/app.py` | Add shadow mode toggle to War Room config panel |
| `scripts/init-db.sql` | Seed `shadow_mode = true` in system_config defaults |

### Specific Actions

**1a. Set the flag in system_config**
```sql
INSERT INTO system_config (key, value) VALUES ('shadow_mode', 'true')
  ON CONFLICT (key) DO UPDATE SET value = 'true';
```
Or via Python: `await set_config("shadow_mode", True)`

**1b. Modify `titan/pipeline/email_send.py`**

In `send_emails()` (line ~238), add a shadow mode check at the top of the function, before the batch query:

```python
shadow = await get_config("shadow_mode", False)
if shadow:
    # Log what WOULD happen, but don't touch Instantly
    leads = await fetch_all(
        """SELECT id, business_name, email, subject, body
           FROM clients WHERE status = 'email_queued'
           ORDER BY created_at ASC LIMIT %s""",
        (batch_size,),
    )
    for lead in leads:
        logger.info("SHADOW: would send to %s <%s> — subject: %s",
                     lead["business_name"], lead["email"], lead["subject"])
        await emit_event("shadow_email_send", {
            "client_id": lead["id"],
            "email": lead["email"],
            "subject": lead["subject"],
        })
    return
```

Key detail: Do NOT transition the lead state. Leads stay at `email_queued` so they can be sent for real in Phase 4 by flipping the flag off.

**1c. Modify `titan/pipeline/build_site.py`**

In `_build_full_site()`, after the site HTML is generated but before calling `netlify_deploy.deploy_static_site()`, check the flag:

```python
shadow = await get_config("shadow_mode", False)
if shadow:
    logger.info("SHADOW: built site for %s locally — skipping Netlify deploy", lead["business_name"])
    # Save the generated HTML to a local directory for review
    output_dir = Path(f"shadow_sites/{lead['id']}_{lead['business_name']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, html in pages.items():
        (output_dir / filename).write_text(html)
    await emit_event("shadow_site_built", {
        "client_id": lead["id"],
        "business_name": lead["business_name"],
        "local_path": str(output_dir),
        "page_count": len(pages),
    })
    return str(output_dir)
```

Save built sites to `shadow_sites/` so Nico can review them locally via the War Room or file browser.

**1d. Modify `titan/pipeline/follow_up.py`**

Same pattern as email_send -- check `shadow_mode` at the top of `process_follow_ups()`. Log what would be sent, do not transition state.

**1e. Modify `titan/pipeline/invoice.py`**

At the top of `process_invoices()`, return early if shadow mode:

```python
shadow = await get_config("shadow_mode", False)
if shadow:
    logger.info("SHADOW: invoice stage skipped entirely")
    return
```

**1f. Modify `titan/pipeline/deploy_site.py`**

Same pattern -- skip verification loops during shadow mode since nothing is deployed.

**1g. Add War Room toggle**

In `hermes/web/app.py`, the config panel (line ~716) already lists editable keys. Add `"shadow_mode"` to the `EDITABLE_CONFIG_KEYS` list so Nico can toggle it from the War Room UI.

### Verification
- [ ] `await get_config("shadow_mode")` returns `True`
- [ ] Run `send_emails()` manually -- confirm logs show "SHADOW: would send" and NO Instantly API calls
- [ ] Run `build_sites()` manually -- confirm site HTML exists in `shadow_sites/` and NO Netlify API calls
- [ ] Run `process_invoices()` -- confirm it returns immediately with shadow log
- [ ] Toggle shadow_mode off in War Room -- confirm pipeline resumes normal behavior
- [ ] Run `ruff check titan/pipeline/` -- no lint errors
- [ ] Run `pytest tests/test_email_campaign_setup.py tests/test_compliance.py -v` -- all pass

### Dependencies
- Phase 2 complete (100+ leads in `discovered` or `researched` status)
- `system_config` table exists (it does -- used by `titan_paused`, `review_mode`, etc.)

### Effort
**4-6 hours**. Mostly straightforward flag checks. The build_site local save needs the most care to ensure file paths are correct and reviewable.

---

## Task 2: Run Full Pipeline (Shadow)

### Objective
Process 100+ leads through every pipeline stage, monitoring for errors, measuring throughput, and identifying bottlenecks. This is the stress test.

### Files Involved
| File | Role |
|------|------|
| `titan/daemon.py` | Main loop -- processes task_queue entries |
| `perseus/scheduler.py` | Schedules pipeline tasks on cron |
| `titan/pipeline/lead_discovery.py` | Stage 1 -- already done in Phase 2, but verify |
| `titan/pipeline/lead_research.py` | Stage 2 -- Firecrawl + LLM research |
| `titan/pipeline/email_compose.py` | Stage 3 -- Claude writes emails |
| `titan/pipeline/email_send.py` | Stage 4 -- shadow-blocked |
| `titan/pipeline/build_site.py` | Stage 8 -- builds locally only |
| `titan/state_machine.py` | State transitions -- watch for stuck leads |
| `hermes/web/app.py` | War Room monitoring |
| `logs/titan.log` | Error tracking |

### Specific Actions

**2a. Trigger pipeline run from War Room**

Use the existing "Run Full Pipeline" button in War Room (calls `POST /api/pipeline/run`), which inserts tasks into `task_queue` for all stages. Or manually:

```python
await insert_task("lead_research", {})
await insert_task("email_compose", {})
await insert_task("email_send", {})
await insert_task("build_sites", {})
```

**2b. Monitor daemon logs in real-time**

```bash
# Terminal 1: Watch for errors
tail -f logs/titan.log | grep -E "ERROR|CRITICAL|SHADOW"

# Terminal 2: Watch pipeline progress
tail -f logs/titan.log | grep -E "researched|composed|queued|SHADOW"

# Terminal 3: Database state
watch -n 10 'psql -c "SELECT status, COUNT(*) FROM clients GROUP BY status ORDER BY status"'
```

**2c. Track throughput metrics**

Query after each batch completes:

```sql
-- Leads per stage
SELECT status, COUNT(*) as count,
       MIN(updated_at) as first,
       MAX(updated_at) as last,
       EXTRACT(EPOCH FROM MAX(updated_at) - MIN(updated_at)) / COUNT(*) as seconds_per_lead
FROM clients
WHERE updated_at > NOW() - INTERVAL '1 day'
GROUP BY status
ORDER BY count DESC;

-- Bottleneck detection: which stage takes longest?
SELECT
  prev_status,
  next_status,
  AVG(transition_seconds) as avg_seconds,
  MAX(transition_seconds) as max_seconds,
  COUNT(*) as transitions
FROM (
  SELECT status as next_status,
         LAG(status) OVER (PARTITION BY id ORDER BY updated_at) as prev_status,
         EXTRACT(EPOCH FROM updated_at - LAG(updated_at) OVER (PARTITION BY id ORDER BY updated_at)) as transition_seconds
  FROM clients
) sub
WHERE prev_status IS NOT NULL
GROUP BY prev_status, next_status
ORDER BY avg_seconds DESC;
```

**2d. Watch for known failure patterns**

Based on the audit findings (project_critical_bugs.md), actively watch for:
- JSON parsing failures in lead_research.py (LLM returns malformed JSON)
- A2A timeouts when build_site.py calls ClawdBot
- State machine stuck leads (no valid transition from current state)
- Memory leaks in daemon loop (Mac Studio has 32GB -- should be plenty, but verify)
- psycopg connection pool exhaustion under concurrent research tasks

**2e. Pipeline completion target**

Run until at least:
- 100+ leads reach `researched` status
- 80+ leads reach `email_drafted` status
- 80+ leads reach `email_queued` status (shadow-blocked at send)
- 10+ leads have shadow site builds in `shadow_sites/`

### Verification
- [ ] `SELECT COUNT(*) FROM clients WHERE status NOT IN ('discovered', 'lost')` > 100
- [ ] `grep -c "SHADOW: would send" logs/titan.log` > 80
- [ ] `grep -c "SHADOW: built site" logs/titan.log` > 10
- [ ] `grep -c "ERROR" logs/titan.log` < 5 (some errors are expected on edge cases)
- [ ] `grep -c "CRITICAL" logs/titan.log` = 0
- [ ] No daemon restarts required during the run
- [ ] All 5 daemons show healthy in `make status`

### Dependencies
- Task 1 complete (shadow mode enabled)
- All daemons running (`make start`)
- Docker services up (`make health` -- Postgres, Qdrant)

### Effort
**8-12 hours** of wall-clock time for the pipeline to process. Active monitoring time: ~3 hours spread across the run. Most of it is waiting for LLM calls and Firecrawl scrapes.

---

## Task 3: Quality Review

### Objective
Nico manually reviews generated artifacts in War Room to determine if quality is production-ready. This is the human-in-the-loop gate before real money is on the line.

### Files Involved
| File | Role |
|------|------|
| `hermes/web/app.py` | War Room -- review queue UI |
| `hermes/web/frontend/` | React War Room frontend |
| `titan/review_mode.py` | Review queue logic (`get_pending_reviews()`, `approve_review()`) |
| `titan/pipeline/email_compose.py` | Email template quality |
| `soul/soul_copy.md` | Copywriting guidelines -- update if emails are off-brand |
| `templates/` | Industry website templates -- update if sites need work |
| `shadow_sites/` | Locally built demo sites for review |

### Specific Actions

**3a. Review 20+ generated emails**

Open War Room dashboard. Navigate to the review queue. For each email:

- Is the subject line compelling and under 55 characters?
- Does the body reference the prospect's actual business (not generic)?
- Is the personalization hook genuine (from Firecrawl research, not hallucinated)?
- Does the CTA make sense for the industry?
- Is it in the correct language for the prospect's country?
- Does it pass the "would I open this?" test?
- Check spam simulation scores in `review_queue.metadata`

Score each as: **Sendable** / **Needs Edit** / **Reject**

Target: 80%+ rated "Sendable" without edits.

**3b. Review 10+ generated demo sites**

Navigate to `shadow_sites/` directory. For each site:

- Open `index.html` in a browser
- Check all 5 pages load (Home, About, Services, Contact, Blog/Portfolio)
- Is the business name and industry correct?
- Are images placeholder or generated? (Recraft integration)
- Is the design professional enough to show a prospect?
- Does the contact form exist (even if non-functional in demo)?
- Is the copy relevant to the specific business?
- Mobile responsiveness (resize browser window)

Score each as: **Showable** / **Needs Work** / **Reject**

Target: 70%+ rated "Showable" without edits.

**3c. Review lead research quality**

```sql
SELECT business_name, email, industry, city, country,
       LEFT(research_summary, 200) as summary_preview,
       LENGTH(research_summary) as summary_length,
       research_facts IS NOT NULL as has_facts
FROM clients
WHERE status IN ('researched', 'email_drafted', 'email_queued')
ORDER BY created_at DESC
LIMIT 30;
```

For each lead, verify:
- Is this a real business? (not a directory listing, not defunct)
- Is the email a real person? (not info@, not support@)
- Does the research summary contain real facts about the business?
- Is the industry classification correct?
- Does the business actually need a website? (check if they already have a good one)

Target: 90%+ are real businesses that genuinely need websites.

**3d. Document feedback for template improvements**

Create a feedback file:

```
shadow_sites/REVIEW_NOTES.md
```

For each issue pattern found, document:
- What was wrong (e.g., "Restaurant sites missing menu section")
- Which template or prompt to fix
- Severity (blocks launch vs. nice-to-have)
- Suggested fix

### Verification
- [ ] 20+ emails reviewed with scores recorded
- [ ] 10+ sites reviewed with scores recorded
- [ ] 30+ leads spot-checked for research quality
- [ ] `REVIEW_NOTES.md` created with all feedback
- [ ] 80%+ emails pass quality bar
- [ ] 70%+ sites pass quality bar
- [ ] 90%+ leads are real businesses
- [ ] Any failing patterns have specific template/prompt fixes identified

### Dependencies
- Task 2 complete (pipeline has processed 100+ leads)
- War Room accessible and review queue populated
- Nico available for ~4 hours of focused review

### Effort
**4-6 hours** of Nico's time. Cannot be parallelized -- this is the human judgment gate.

---

## Task 4: Fix Pipeline Bugs

### Objective
Fix every error surfaced during the shadow run. Re-run failed leads to verify fixes. Zero CRITICAL errors must remain.

### Files Involved
| File | Likely Issues |
|------|---------------|
| `titan/pipeline/lead_research.py` | JSON parsing failures from LLM output |
| `titan/pipeline/email_compose.py` | Skill loader failures, template rendering |
| `titan/pipeline/build_site.py` | A2A timeout calling ClawdBot, HTML generation |
| `titan/state_machine.py` | Stuck leads -- invalid transition attempted |
| `shared/comms.py` | A2A transport errors, timeout handling |
| `shared/llm_client.py` | Claude API rate limits, malformed responses |
| `clawdbot/a2a_server.py` | ClawdBot A2A endpoint errors |
| `shared/db.py` | Connection pool exhaustion under concurrency |

### Specific Actions

**4a. Triage error log**

```bash
# Extract unique error patterns
grep "ERROR" logs/titan.log | sed 's/.*ERROR //' | sort | uniq -c | sort -rn > shadow_errors.txt
grep "CRITICAL" logs/titan.log > shadow_critical.txt

# Find stuck leads
psql -c "SELECT id, business_name, status, updated_at
         FROM clients
         WHERE updated_at < NOW() - INTERVAL '2 hours'
         AND status NOT IN ('lost', 'paid', 'unsubscribed', 'email_queued')
         ORDER BY updated_at ASC;"
```

**4b. Fix JSON parsing failures (most common)**

In `lead_research.py`, the LLM sometimes returns markdown-wrapped JSON. The existing `json.loads()` call fails. Fix:

```python
# Strip markdown code fences before parsing
raw = response.strip()
if raw.startswith("```"):
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
result = json.loads(raw)
```

Verify this pattern exists or add it if missing.

**4c. Fix A2A timeouts**

In `build_site.py`, the `request_task_result()` call to ClawdBot has a 60-second timeout (line ~49). For complex sites, ClawdBot may need more time:

- Increase timeout to 180 seconds for site builds
- Add retry logic with exponential backoff (1 retry, 2x timeout)
- Log the specific stage where timeout occurs

**4d. Fix state machine stuck leads**

Query for leads in unexpected states. For each:
- If lead is stuck at `researched` but has email_drafted data, manually transition
- If lead is stuck at `building` with no site output, reset to `closed` to retry
- Add a "stuck lead recovery" task that runs every 6 hours:

```python
async def recover_stuck_leads():
    """Reset leads stuck in transient states for >2 hours."""
    stuck = await fetch_all(
        """SELECT id, status FROM clients
           WHERE status IN ('building')
           AND updated_at < NOW() - INTERVAL '2 hours'"""
    )
    for lead in stuck:
        await transition_lead(lead["id"], "closed")  # Reset to retry
        logger.warning("Reset stuck lead %s from %s to closed", lead["id"], lead["status"])
```

**4e. Re-run failed leads**

After fixes are applied:

```python
# Reset failed leads to their previous state for re-processing
await execute(
    "UPDATE clients SET status = 'discovered' WHERE status = 'lost' AND lost_reason LIKE '%error%'"
)
# Trigger pipeline re-run
await insert_task("lead_research", {})
await insert_task("email_compose", {})
```

Monitor re-run for the same errors. If they recur, escalate.

### Verification
- [ ] `grep -c "CRITICAL" logs/titan.log` = 0 after fixes
- [ ] `grep -c "ERROR" logs/titan.log` decreased by >50% after fixes
- [ ] All stuck leads either progressed or marked `lost` with a reason
- [ ] Re-run of previously failed leads succeeds
- [ ] `ruff check titan/ shared/` -- no lint errors
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -v` -- all tests pass
- [ ] No regressions in working stages

### Dependencies
- Task 2 and Task 3 complete (errors identified and quality feedback collected)

### Effort
**4-8 hours** depending on bug count. JSON parsing and A2A timeouts are the usual suspects based on the audit findings. State machine fixes are mechanical.

---

## Task 5: Performance Baseline

### Objective
Measure cost, speed, and resource consumption per lead. Compare against the budget model. Adjust LLM tier allocation if costs are too high.

### Files Involved
| File | Role |
|------|------|
| `tools/budget_guard.py` | Spend enforcement and tracking |
| `shared/llm_client.py` | LLM call tracking (model, tokens, cost) |
| `titan/daemon.py` | Daemon memory and cycle metrics |
| `hermes/web/app.py` | War Room metrics endpoint |
| `conway/wallet.py` | Agent wallet balances |

### Specific Actions

**5a. Measure leads per hour**

```sql
-- Throughput by stage over the shadow run period
SELECT status,
       COUNT(*) as total,
       COUNT(*) / GREATEST(EXTRACT(EPOCH FROM MAX(updated_at) - MIN(updated_at)) / 3600, 1) as leads_per_hour
FROM clients
WHERE updated_at > NOW() - INTERVAL '2 days'
GROUP BY status
ORDER BY leads_per_hour ASC;  -- Slowest first = bottleneck
```

Target: >20 leads/hour through research, >30 leads/hour through compose.

**5b. Measure API cost per lead**

```sql
-- If LLM call tracking exists in events table
SELECT
  COUNT(DISTINCT c.id) as leads_processed,
  COUNT(e.id) as total_llm_calls,
  SUM((e.payload->>'cost')::numeric) as total_cost,
  SUM((e.payload->>'cost')::numeric) / NULLIF(COUNT(DISTINCT c.id), 0) as cost_per_lead,
  SUM((e.payload->>'tokens')::integer) as total_tokens
FROM events e
JOIN clients c ON (e.payload->>'client_id')::int = c.id
WHERE e.event_type LIKE 'llm_%'
AND e.created_at > NOW() - INTERVAL '2 days';
```

Alternatively, check budget_guard tracking:

```python
from tools.budget_guard import BudgetGuard
guard = BudgetGuard()
status = await guard.check_budget()
# status contains: spent_today, daily_limit, remaining
```

Target: <$0.50 per lead through full pipeline (research + compose + build = ~3-5 LLM calls).

**5c. Measure daemon memory usage**

```bash
# Memory per daemon process
ps aux | grep -E "titan|perseus|hermes|clawdbot|conway" | awk '{print $11, $6/1024 "MB"}'

# Track over time (run every 5 minutes during shadow run)
while true; do
  date >> shadow_memory.log
  ps aux | grep -E "python.*daemon" | awk '{print $11, $6/1024 "MB"}' >> shadow_memory.log
  sleep 300
done
```

Target: Each daemon under 2GB RSS. Total system under 16GB (half of 32GB Mac Studio).

**5d. Compare against budget model**

| Metric | Budget Target | Shadow Actual | Status |
|--------|--------------|---------------|--------|
| Cost per lead (full pipeline) | <$0.50 | ??? | |
| Leads per hour (research) | >20 | ??? | |
| Leads per hour (compose) | >30 | ??? | |
| Daemon memory (total) | <16GB | ??? | |
| Daily API spend | <$27/day ($800/mo) | ??? | |

**5e. LLM tier optimization**

If costs exceed budget:

1. Check which stages use Opus vs. Sonnet vs. Haiku in `shared/llm_client.py`
2. Downgrade research summarization from Opus to Sonnet (saves ~60% per call)
3. Downgrade email composition from Opus to Sonnet if quality review (Task 3) shows Sonnet quality is acceptable
4. Keep site building on Opus (quality matters most here)
5. Move all scoring/classification calls to Haiku

Update `shared/llm_client.py` or relevant pipeline files with model overrides:

```python
# In lead_research.py
response = await llm(prompt, model="sonnet")  # Was: opus

# In email_compose.py
response = await llm(prompt, model="sonnet")  # Was: opus
```

### Verification
- [ ] Throughput measurements recorded for all stages
- [ ] Cost per lead calculated and documented
- [ ] Memory usage measured for all daemons
- [ ] Budget comparison table filled in
- [ ] If over budget: LLM tier adjustments made and re-tested
- [ ] Adjusted cost per lead still produces acceptable quality (re-check Task 3 scores)
- [ ] Results documented in `shadow_sites/PERFORMANCE_BASELINE.md`

### Dependencies
- Task 2 complete (enough data to measure)
- Task 4 complete (errors fixed so measurements are clean)
- Budget model from Phase 2 planning

### Effort
**2-4 hours**. Mostly SQL queries and log analysis. LLM tier optimization may require re-running a small batch and re-reviewing quality.

---

## Success Criteria Checklist

| Criterion | Metric | Gate |
|-----------|--------|------|
| Pipeline completeness | 100+ leads processed through full pipeline | HARD |
| Zero critical errors | 0 CRITICAL entries in any daemon log | HARD |
| Email quality | 20+ emails reviewed, 80%+ deemed "sendable" by Nico | HARD |
| Site quality | 10+ sites reviewed, 70%+ deemed "showable" by Nico | HARD |
| Cost within budget | Per-lead cost within 20% of budget model | SOFT |
| Daemon stability | All daemons stay healthy for 24+ hours | HARD |
| Memory within limits | Total daemon memory < 16GB on Mac Studio | HARD |
| State machine clean | Zero stuck leads at end of shadow run | SOFT |

HARD gates must pass before Phase 4. SOFT gates should pass but can be addressed in parallel with Phase 4 start.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM returns malformed JSON crashing research stage | High | Medium | JSON fence stripping already partially implemented; add robust fallback parsing |
| ClawdBot A2A timeout on complex sites | Medium | Medium | Increase timeout to 180s, add retry with backoff |
| Firecrawl rate limit during batch research | Medium | Low | Semaphore already at 4 concurrent; add exponential backoff on 429 |
| Email quality too low for send | Medium | High | Update soul_copy.md prompts, re-run compose with better instructions |
| Site quality too low for demos | Medium | High | Update templates/ with Nico's feedback, re-run builds |
| Budget exceeded during shadow run | Low | Medium | Shadow mode prevents real sends anyway; adjust tiers before Phase 4 |
| Postgres connection pool exhaustion | Low | High | Monitor pool stats; increase pool size if needed in shared/db.py |
| Mac Studio memory pressure | Low | Medium | Monitor RSS per daemon; kill and restart if any exceeds 4GB |

---

## Timeline

| Day | Activity |
|-----|----------|
| Day 14 | Implement shadow mode flag (Task 1). Unit test shadow gates. |
| Day 15 | Start full pipeline run (Task 2). Monitor first 50 leads through. |
| Day 16 | Pipeline continues. Fix any errors found so far (Task 4, partial). |
| Day 17 | Quality review (Task 3). Nico reviews emails and sites in War Room. |
| Day 18 | Fix remaining bugs (Task 4). Performance baseline (Task 5). |
| Day 19 | Re-run failed leads. Final review. Sign off on success criteria. |

---

## Exit to Phase 4

When all HARD success criteria pass:

1. Flip `shadow_mode = false` in War Room
2. Keep `review_mode = true` (Nico approves every email before Instantly sends)
3. Set `daily_email_cap` to 10 (start slow)
4. Ensure `titan_paused = false`
5. Phase 4 begins: first real emails go out with full human oversight
