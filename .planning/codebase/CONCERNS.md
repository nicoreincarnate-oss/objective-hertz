# Codebase Concerns

**Analysis Date:** 2026-03-27

## P0 — Launch Blockers (Must Fix Before First Email)

### Missing Unsubscribe Endpoint (CAN-SPAM Violation)

- Issue: `titan/compliance.py` generates HMAC-signed unsubscribe URLs (`/unsub?id=...&sig=...`) that point to `unsubscribe_base_url` from `system_config`. However, **no HTTP handler exists anywhere in the codebase** to serve that route. Every email sent includes a dead unsubscribe link. This is a federal CAN-SPAM violation (up to $50,120 per email) and will also damage sender reputation when recipients report spam because the unsub link is broken.
- Files: `titan/compliance.py` (lines 51-56 generate the link), `hermes/web/app.py` (no `/unsub` route)
- Impact: Legal liability, domain blacklisting, Instantly account suspension
- Fix approach: Add a `/unsub` route to `hermes/web/app.py` that:
  1. Validates the HMAC signature against `UNSUBSCRIBE_SECRET`
  2. Calls `UPDATE clients SET status = 'unsubscribed' WHERE id = %s`
  3. Returns a confirmation page
  4. Does NOT require authentication (public endpoint)
  5. Deploy this handler on whatever domain `unsubscribe_base_url` points to

### Compliance Config Not Seeded in Database

- Issue: `titan/compliance.py:assert_compliance_ready()` reads `company_address` and `unsubscribe_base_url` from the `system_config` table (via `get_config()`). These are NOT populated by `scripts/init-db.sql` and NOT set by environment variables. They must be manually inserted into the database before Titan can send any email. If missing, Titan daemon startup emits an alert but **continues running** (the `RuntimeError` is caught as non-fatal in `titan/daemon.py` line 147).
- Files: `titan/compliance.py` (lines 67-75), `titan/daemon.py` (lines 145-148), `scripts/init-db.sql`
- Impact: Emails blocked until manual DB insert; or worse, if the compliance check is bypassed, emails go out without physical address footer
- Fix approach:
  1. Add `company_address` and `unsubscribe_base_url` to `scripts/setup-perseus.sh` setup flow
  2. Make `assert_compliance_ready()` failure truly fatal (do not catch the exception in `titan/daemon.py`)
  3. Seed default placeholder values in `init-db.sql` that fail the placeholder check

### UNSUBSCRIBE_SECRET Not in Setup Flow

- Issue: `titan/compliance.py` reads `UNSUBSCRIBE_SECRET` from environment. It must be 16+ characters and not a placeholder. This is not in `shared/config.py` (unlike all other env vars) and is not documented in setup scripts.
- Files: `titan/compliance.py` (line 69), `shared/config.py` (not present)
- Impact: Silent compliance failure — emails won't send but error is non-obvious
- Fix approach: Add `UNSUBSCRIBE_SECRET` to `shared/config.py` and the setup script, with auto-generation of a random 32-char secret

### Instantly Account Setup Prerequisites

- Issue: Before any email can be sent, Instantly requires:
  1. `INSTANTLY_API_KEY` set in `.env`
  2. At least one email sending account connected in Instantly dashboard
  3. Sending account warmed up (Instantly warmup takes 2-3 weeks minimum)
  4. Campaign template must include `{{compliance_footer}}` variable (compliance.py line 298 sends this as a template variable but Instantly must be configured to render it)
- Files: `tools/instantly_client.py`, `titan/compliance.py` (line 298), `titan/pipeline/email_send.py`
- Impact: Without warmup, emails go to spam. Without `{{compliance_footer}}` in template, physical address and unsub link are silently dropped.
- Fix approach: Add a pre-flight check that verifies at least one account exists and is active via `InstantlyClient.list_accounts()`, and document the Instantly template requirement

## P0 — Payment Pipeline Gaps

### Stripe Payment Links Are Not Invoices

- Issue: `tools/payment_router.py` creates Stripe Payment Links, not proper Stripe Invoices. Payment Links are anonymous — no customer record is created in Stripe, no tax handling, no proper invoice PDF. For a business selling website services, this likely fails accounting/tax compliance requirements.
- Files: `tools/payment_router.py` (lines 103-150)
- Impact: No proper invoice trail for tax purposes, no customer management in Stripe
- Fix approach: Switch to Stripe Checkout Sessions with `mode='payment'` and `invoice_creation={'enabled': True}`, or use the Stripe Invoicing API directly

### Wise Integration Creates Outbound Transfers, Not Payment Requests

- Issue: `_create_wise_invoice()` in `tools/payment_router.py` creates a Wise *transfer* (outbound payment), not an incoming payment request. The function creates a quote and transfer FROM the operator's account, which is the opposite of collecting payment. The `url` field returns empty because Wise transfers have no customer-facing payment page.
- Files: `tools/payment_router.py` (lines 285-358)
- Impact: Wise "invoices" result in the operator sending money OUT, not collecting it. This is fundamentally broken for revenue collection.
- Fix approach: Either remove Wise as a payment collection method (it's not designed for this), or integrate Wise's Request Money feature if available. Stripe should be the primary/only payment method.

### No Webhook for Payment Confirmation

- Issue: Payment detection relies on polling (`_check_stripe_payments` polls Stripe API every 2 hours per scheduler). There is no Stripe webhook endpoint to receive real-time payment confirmations. A customer who pays immediately after receiving an invoice won't be recognized as paid for up to 2 hours.
- Files: `titan/pipeline/invoice.py` (line 272 `_check_payments`), `perseus/scheduler.py` (line 34, 7200s interval)
- Impact: Delayed payment recognition, poor customer experience, risk of duplicate invoice sends
- Fix approach: Add a Stripe webhook endpoint at `hermes/web/app.py` that handles `payment_intent.succeeded` events and transitions deals to `paid` in real time

## P1 — Operational Risks

### ClawdBot Single Point of Failure for Site Building

- Issue: Site building (`titan/pipeline/build_site.py`) depends entirely on ClawdBot via A2A call (`call_agent_capability("clawdbot", "build_full_site", ...)`). If ClawdBot is down, all site builds fail. There is no fallback, no retry scheduling, and the lead gets stuck in `building` status with no automatic recovery path.
- Files: `titan/pipeline/build_site.py` (lines 101-109), `clawdbot/site_builder.py`
- Impact: Pipeline bottleneck — closed deals cannot progress to deployment/invoicing if ClawdBot is unhealthy
- Fix approach:
  1. Add a timeout-based retry that re-enqueues `build_sites` tasks when ClawdBot A2A fails
  2. Transition lead back to `closed` (not stuck in `building`) on ClawdBot failure so retry is possible
  3. Consider a minimal fallback site builder that doesn't require ClawdBot's 5-agent competitive process

### Review Mode Blocks Autonomous Operation

- Issue: `REVIEW_MODE` defaults to `True` in `shared/config.py` (line 192). When True, `email_send.py` queues all emails for manual approval via `review_queue` table instead of sending them. This is by design for the first 10 sales, but: (a) there's no dashboard UI to approve queued emails programmatically, (b) the review queue can only be managed via direct DB queries.
- Files: `shared/config.py` (line 192), `titan/pipeline/email_send.py` (lines 241-243)
- Impact: System cannot send ANY email autonomously until `review_mode` is manually set to False in the database
- Fix approach: The War Room dashboard needs an email approval interface, or add a clear `make approve-emails` command. Document the review mode exit criteria.

### Warm-Up Phase Blocks Significant Volume

- Issue: `titan/deliverability.py` enforces a 21-day warmup ramp starting at 10 emails/day. `warm_up_phase` defaults to `True` in `system_config`. During warmup, daily volume is capped per the `WARMUP_RAMP` dict (day 1=10, day 7=50, day 14=175, day 21=600).
- Files: `titan/deliverability.py` (lines 28-34)
- Impact: Revenue ramp is physically gated by warmup schedule. Day 1 = max 10 prospects.
- Fix approach: This is correct behavior for deliverability. Plan revenue expectations around the warmup curve. Ensure `warmup_day` counter advances correctly (check `_enforce_warmup_volume` advances daily).

### Site Build QA Gate Can Block Deployment Indefinitely

- Issue: `build_site.py` and `close_deal.py` both require QA verification via `request_task_result("verify_demo_site", ...)`. If ClawdBot's verification capability is unavailable, sites either: (a) don't deploy (default), or (b) deploy without QA if `SITE_BUILD_FAIL_OPEN_WHEN_QA_UNAVAILABLE=true`. Option (a) silently blocks the pipeline; option (b) risks deploying broken sites.
- Files: `titan/pipeline/build_site.py` (lines 40-93), `titan/pipeline/close_deal.py` (lines 66-77), `shared/config.py` (line 157)
- Impact: Leads stuck in `building` or `interested` status with no automatic recovery
- Fix approach: Add monitoring/alerting when QA is consistently unavailable, and implement a manual override in the War Room dashboard

## P1 — Data Integrity Risks

### Task Queue Race Condition (Mitigated but Not Eliminated)

- Issue: `claim_task()` in `shared/agent_base.py` uses `UPDATE ... WHERE status = 'pending' RETURNING id` which is safe against double-claiming due to Postgres row-level locking. However, `get_pending_tasks()` (not shown but implied) likely reads tasks without `FOR UPDATE SKIP LOCKED`, meaning two agents could read the same task list and both attempt to claim the same task (one would fail harmlessly).
- Files: `shared/agent_base.py` (lines 221-233)
- Impact: Low — the claim is atomic, so at most one wasted attempt per race. No duplicate execution.
- Fix approach: Use `SELECT ... FOR UPDATE SKIP LOCKED` in `get_pending_tasks()` for cleaner task distribution

### Idempotency Key Collision in Invoice Creation

- Issue: `_invoice_idempotency_key()` in `tools/payment_router.py` hashes `email|amount|currency|description`. If the same client is invoiced twice for the same amount and description (e.g., two separate website projects), the idempotency key collides and Stripe returns the original payment link instead of creating a new one.
- Files: `tools/payment_router.py` (lines 461-464), `titan/pipeline/invoice.py` (line 107 uses `f"website:{lead['id']}:{amount}"`)
- Impact: Low — invoice.py uses `client_id` in the key which makes it unique per client. The generic `_invoice_idempotency_key` function is only used as a fallback.
- Fix approach: Always pass explicit `idempotency_key` from invoice.py (already done). Consider deprecating the generic function.

### Email Sequence Dedup Relies on (client_id, step) Unique Constraint

- Issue: `email_compose.py` uses `ON CONFLICT (client_id, step) DO NOTHING` to prevent duplicate emails. This means a client can only ever have one email per step number. If a client re-enters the pipeline (e.g., comes back months later for a second project), they cannot receive new step-1 emails.
- Files: `titan/pipeline/email_compose.py` (lines 139-143, 217-223)
- Impact: Returning customers cannot be re-engaged through the email pipeline
- Fix approach: Add a `campaign_id` or `engagement_id` to the unique constraint: `(client_id, step, campaign_context)`

## P1 — Budget and Cost Control

### LLM Cost Tracking Depends on Estimation, Not Actual Usage

- Issue: `shared/llm_client.py` estimates token costs using hardcoded rates (`_COST_PER_1K` dict) and approximate token counts. Actual API responses include precise usage data, but the budget tracker uses estimates. Over time, this drift could cause budget overruns or premature budget exhaustion.
- Files: `shared/llm_client.py` (lines 47-51)
- Impact: Budget could be 20-30% inaccurate depending on prompt/completion ratio
- Fix approach: Parse actual `usage` field from Claude API responses and record real costs

### No Per-Stage Budget Allocation

- Issue: `BudgetGuard` tracks a single `monthly_cap` across ALL spending categories. There is no way to limit, say, Claude API costs to $500 while allowing $300 for other services. A runaway discovery stage could exhaust the entire budget before any emails are composed.
- Files: `tools/budget_guard.py` (lines 13-16)
- Impact: Single greedy pipeline stage can starve all others
- Fix approach: Add per-category caps (e.g., `llm_cap`, `instantly_cap`, `firecrawl_cap`) in addition to the global cap

### Budget Check Only Runs Every Hour

- Issue: Perseus schedules `budget_check` every 3600 seconds (1 hour). In a burst scenario, the system could spend significantly before the next check. The per-call `can_spend()` check exists but is not called before every LLM invocation.
- Files: `perseus/scheduler.py` (line 41), `tools/budget_guard.py`
- Impact: Up to 1 hour of unchecked spending between budget enforcement passes
- Fix approach: Integrate `can_spend()` into `llm_client.py` before every Claude API call (not just as a scheduled check)

## P2 — Security Considerations

### Dashboard Secret in Environment Variable

- Issue: `hermes/web/app.py` uses `DASHBOARD_SECRET` env var for authentication. Session cookies are HMAC-signed with a per-process random key, meaning all sessions invalidate on restart. The auth model is single-operator (one shared secret), which is appropriate for the current use case but has no audit trail of who performed actions.
- Files: `hermes/web/app.py` (lines 43-57)
- Impact: Low for single-operator system, but no access logging
- Fix approach: Add request logging for all authenticated dashboard actions

### Docker Compose Default Passwords

- Issue: `docker-compose.yaml` contains default passwords as fallbacks: `perseus_secure_2026` for Postgres, `perseus_graph_2026` for Neo4j, `CHANGE_ME` for N8N, `objective_hertz_2026` for Grafana. While these are overridden by `.env` values, if `.env` is missing or incomplete, services start with predictable credentials.
- Files: `docker-compose.yaml` (lines 24, 90, 115, 155)
- Impact: If Docker ports are exposed beyond localhost, services are accessible with default credentials
- Fix approach: Remove default passwords from docker-compose.yaml; fail startup if `.env` is missing

## P2 — Fragile Areas

### LLM JSON Parsing Throughout Pipeline

- Issue: Nearly every pipeline stage parses LLM output by finding `{` and `}` characters: `start = result.find("{"); end = result.rfind("}") + 1; json.loads(result[start:end])`. This is fragile — if the LLM returns nested JSON, markdown code fences, or multiple JSON objects, parsing fails silently.
- Files: `titan/pipeline/email_compose.py` (lines 117-119, 196-198), `titan/pipeline/lead_discovery.py` (lines 124-125, 275-277), `titan/pipeline/close_deal.py`
- Why fragile: LLM output format is non-deterministic. A model update or temperature change can break parsing.
- Fix approach: Use a robust JSON extraction utility that handles code fences, multiple objects, and partial JSON. Consider using Claude's structured output / tool use for guaranteed JSON.

### Instantly API Response Format Assumptions

- Issue: `tools/instantly_client.py` assumes all responses are JSON dicts or lists, but several endpoints (campaign analytics, lead lists) may return paginated responses with different structures depending on the Instantly API version. The client has no response validation.
- Files: `tools/instantly_client.py` (lines 88-106)
- Impact: Silent data loss if API response format changes
- Fix approach: Add response schema validation, at minimum type checking on critical paths

### No Graceful Degradation When Firecrawl Is Down

- Issue: Lead discovery depends on Firecrawl for web search. If Firecrawl is unavailable, `_search_for_businesses()` returns an empty list silently. Discovery skills also fall through to `custom_firecrawl` as the final fallback, but if Firecrawl itself is down, the entire discovery pipeline produces zero leads without loud alerting.
- Files: `titan/pipeline/lead_discovery.py` (lines 371-402), `tools/firecrawl_client.py`
- Impact: Discovery silently stops producing leads. The `_emit_discovery_empty` event exists but may be missed.
- Fix approach: `_emit_discovery_empty` already emits `lead_discovery_empty` events. Ensure Hermes surfaces this as a high-priority Telegram alert after 2+ consecutive empty discovery runs.

## Pre-Launch Configuration Checklist

**Environment variables that MUST be configured:**
1. `INSTANTLY_API_KEY` — Instantly.ai API key for email sending
2. `ANTHROPIC_API_KEY` — Claude API key for all LLM operations
3. `FIRECRAWL_API_KEY` — Firecrawl for web search/lead discovery
4. `STRIPE_API_KEY` — Stripe for payment collection
5. `NETLIFY_AUTH_TOKEN` — Netlify for site deployment
6. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Operator alerts
7. `UNSUBSCRIBE_SECRET` — HMAC signing for unsubscribe links (16+ chars)
8. `DASHBOARD_SECRET` — War Room authentication
9. `POSTGRES_PASSWORD` — Database access

**Database values that MUST be manually set:**
1. `company_address` in `system_config` table — Physical mailing address for CAN-SPAM
2. `unsubscribe_base_url` in `system_config` table — Domain hosting the unsub endpoint

**External service setup required:**
1. Instantly.ai: Connect at least one email sending account, run warmup (2-3 weeks)
2. Instantly.ai: Create email template with `{{compliance_footer}}` variable
3. Stripe: Verify account for live payments (not test mode)
4. Netlify: Verify account has capacity for multiple sites
5. Firecrawl: Verify API key has sufficient credits
6. Ollama: Pull required models (`qwen2.5:14b-instruct-q4_K_M`, `llama3.2:3b`, `nomic-embed-text`)

**Runtime prerequisites:**
1. Docker services running (`make up`)
2. Ollama running with models pulled
3. `review_mode` set to desired value in DB (True = manual approval, False = autonomous)
4. `warm_up_phase` and `warmup_day` initialized in `system_config`

## Dependencies at Risk

### Instantly.ai API Stability

- Risk: The entire outreach pipeline depends on Instantly.ai's v2 API. API changes or account suspension would halt all email operations.
- Impact: Zero outbound email capability
- Migration plan: The `InstantlyClient` is cleanly abstracted. Swap to another ESP (Smartlead, Lemlist) by implementing the same interface.

### Claude API Cost Sensitivity

- Risk: Every email composition, lead research, and follow-up classification requires a Claude API call. At scale (100+ leads/day), API costs could exceed the $800/month budget.
- Impact: Budget exhaustion forces fallback to local Ollama models, which produce lower-quality emails
- Migration plan: The LLM client already supports Ollama fallback. Quality may degrade but operations continue. Consider caching common classifications.

## Test Coverage Gaps

### No Integration Tests for Payment Flow

- What's not tested: End-to-end payment creation through Stripe, payment detection, deal status transition
- Files: `tools/payment_router.py`, `titan/pipeline/invoice.py`
- Risk: Payment bugs discovered in production could mean lost revenue or double-charging
- Priority: High

### No Integration Tests for Compliance Gate

- What's not tested: Full send path through `titan/compliance.py` → `InstantlyClient` with real API
- Files: `titan/compliance.py`, unit tests exist at `tests/test_compliance.py` but mock everything
- Risk: Compliance footer injection or unsubscribe link could silently fail in production
- Priority: High

### No Test for Unsubscribe Flow (Because It Doesn't Exist)

- What's not tested: Recipient clicking unsubscribe link → client status updated
- Files: No endpoint exists
- Risk: Legal violation
- Priority: Critical

---

*Concerns audit: 2026-03-27*
