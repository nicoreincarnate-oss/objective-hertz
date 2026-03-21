# Fix Everything + Add ClawdBot — Make PERSEUS a 24/7 Machine

## 10 Fixes + 1 New Daemon

### 1. Create `tools/runtime_honesty.py`
Firecrawl client imports `env_is_configured` and `truth_payload` from this missing module. Create it:
- `env_is_configured(key)` → bool: env var set and non-empty
- `truth_payload(mode, data_source, available, *, summary, provider, **kw)` → dict with mode/data_source/available/summary/provider keys

### 2. Create `tools/payment_router.py`
Invoice pipeline imports `PaymentRouter`. Create it with Stripe (primary) + Wise (fallback):
- `create_invoice()` → generates Stripe payment link or Wise invoice
- `check_new_payments()` → polls both for new payments
- Config from `shared.config.payment`

### 3. Fix lead discovery — allow leads without email
`_store_lead()` returns None when email is empty, but `_search_for_businesses()` always returns empty email. Fix: store leads without email using business_name+source dedup. They get enriched with email during the research stage.

### 4. Fix `.env.example` — SMARTLEAD → INSTANTLY
Line 37: `SMARTLEAD_API_KEY` → `INSTANTLY_API_KEY`

### 5. Fix proposal sending in `close_deal.py`
`_send_proposal()` calls `InstantlyClient.send_email()` which doesn't exist. Fix: add `send_one_off_email()` to `InstantlyClient` that uses the existing `send_test_email()` with first available account.

### 6. Fix analytics schema mismatch in `email_send.py`
`sync_campaign_analytics()` writes `campaign_id` but table has `campaign` + `domain` columns. Fix column names and ON CONFLICT clause to `(date, domain, campaign)`.

### 7. Add missing task handlers in Titan daemon
Perseus schedules `health_check`, `budget_check`, `morning_briefing` — Titan ignores them. Add handlers:
- `health_check` → check all agents' heartbeats, emit health event
- `budget_check` → run BudgetGuard, emit budget event
- `morning_briefing` → emit morning_briefing event (Hermes picks it up)

### 8. Wire morning briefing in Hermes alerts
`dispatch_alerts()` only handles generic events. Add: when event_type is `morning_briefing`, call `send_morning_briefing()` directly.

### 9. Fix `init-db.sql` — add `instantly_id` column alias
`email_sequences` table has `smartlead_id`. Add `instantly_id` column.

### 10. Add ClawdBot Daemon (4th agent)
Create `clawdbot/` directory with:
- `clawdbot/daemon.py` — ClawdBot daemon that:
  - Registers as agent with Perseus
  - Processes task types: `skill_execute`, `browser_task`, `web_scrape`, `site_verify`
  - Runs installed skills from all 4 skill directories
  - Uses Firecrawl for web scraping tasks
  - Handles browser-use automation for site verification
- `clawdbot/__init__.py`
- Add ClawdBot task types to Perseus scheduler (skill_execute on demand, site_verify hourly)
- Add ClawdBot to `scripts/start-perseus.sh`
- Wire into Titan: when Titan needs a skill executed or site verified, it inserts a task for ClawdBot

## Execution order
1-2 first (unblock imports), then 3-9 (fixes), then 10 (ClawdBot)
