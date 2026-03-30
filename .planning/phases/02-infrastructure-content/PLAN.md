# Phase 2: Infrastructure + Lead Pipeline (Days 7-14)

**Goal**: Stable Mac Studio with all daemons running. Email templates ready. 100+ pre-qualified leads in the pipeline. Sales playbook documented.

**Timeline**: Days 7-14 (April 3 - April 10, 2026)
**Depends on**: Phase 1 (legal blockers resolved, warmup running, DNS authenticated)

---

## Task 1: Mac Studio Setup

**Effort**: 4-6 hours (Day 7-8)
**Owner**: Nico
**Dependencies**: Mac Studio arrives Day 4 (March 31)

### 1.1 Base System

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Install Homebrew, git, Python 3.11+ | `python3 --version` returns 3.11+ |
| 2 | Install Docker Desktop for Mac | `docker --version` and `docker compose version` |
| 3 | Install Ollama | `ollama --version` |
| 4 | Clone repo: `git clone <repo-url> ~/objective-hertz` | `ls ~/objective-hertz/orchestrator.py` exists |
| 5 | Create Python venv: `python3 -m venv .venv && source .venv/bin/activate` | `which python` points to `.venv/` |
| 6 | Install deps: `pip install -r requirements.txt` | `python -c "import psycopg; import httpx; import fastapi"` |

### 1.2 Docker Services

Start all containers defined in `docker-compose.yaml`:

| Service | Container | Port | Memory | Health Check |
|---------|-----------|------|--------|-------------|
| Postgres 16.2 | perseus-postgres | 5432 | 384MB | `pg_isready -U perseus` |
| Qdrant 1.8.4 | perseus-qdrant | 6333 | 512MB | TCP port check |
| Mem0 | perseus-mem0 | 8888 | 512MB | HTTP /health |
| Neo4j 5 | perseus-neo4j | 7474, 7687 | 512MB | HTTP /7474 |
| N8N 1.30.1 | perseus-n8n | 5678 | 512MB | HTTP /healthz |
| Prometheus | objective-hertz-prometheus | 9090 | 256MB | config-based |
| Grafana | objective-hertz-grafana | 3001 | 256MB | built-in |

**Actions**:
```
cd ~/objective-hertz
make up          # Starts all Docker services + Ollama
docker compose ps  # All containers should show "healthy"
```

**Files involved**: `docker-compose.yaml`, `ops/prometheus/prometheus.yml`, `ops/grafana/provisioning/`

### 1.3 Ollama Models

Pull the three required models (total ~12GB disk):

| Model | Purpose | Config Reference |
|-------|---------|-----------------|
| `qwen2.5:14b-instruct-q4_K_M` | Primary LLM (Mem0, lead research, email drafting) | `shared/config.py` OllamaConfig.model |
| `llama3.2:3b` | Fast secondary (simple tasks, summarization) | `shared/config.py` OllamaConfig.secondary |
| `nomic-embed-text` | Embedding model (Qdrant vectors, Mem0) | `shared/config.py` OllamaConfig.embed_model |

**Actions**:
```
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

**Verification**: `curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin)['models']]"` lists all 3 models.

**RAM estimate**: qwen2.5:14b-q4 uses ~10GB VRAM, llama3.2:3b uses ~2GB, nomic-embed uses ~300MB. With Docker services (~2.9GB) and system overhead, total ~22GB of 32GB.

### 1.4 Environment Configuration

Copy and configure `.env` from the development machine. Run `make setup` (calls `scripts/setup-perseus.sh`) which prompts for all required keys.

**Critical env vars to set** (reference: `shared/config.py`):

| Variable | Source | Config Dataclass |
|----------|--------|-----------------|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | docker-compose defaults | PostgresConfig |
| `ANTHROPIC_API_KEY` | Anthropic console | ClaudeConfig |
| `INSTANTLY_API_KEY` | Instantly dashboard | InstantlyConfig |
| `FIRECRAWL_API_KEY` | Firecrawl dashboard | FirecrawlConfig |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | BotFather / Telegram | TelegramConfig |
| `STRIPE_API_KEY` | Stripe dashboard | PaymentConfig |
| `NETLIFY_AUTH_TOKEN` | Netlify settings | HostingConfig |
| `NEO4J_USER`, `NEO4J_PASSWORD` | docker-compose defaults | MemoryConfig |
| `MONTHLY_BUDGET_CAP` | Set to `800` | BudgetConfig |
| `BUDGET_ALERT_THRESHOLD` | Set to `0.80` | BudgetConfig |

**Files involved**: `.env`, `shared/config.py`, `scripts/setup-perseus.sh`

### 1.5 Database Initialization

**Actions**:
```
# Schema auto-loads via Docker volume mount:
# docker-compose.yaml mounts scripts/init-db.sql to /docker-entrypoint-initdb.d/init.sql
# On fresh Postgres, it runs automatically on first start.

# If Postgres already has data, manually run:
psql -U perseus -h localhost -d perseus -f scripts/init-db.sql
```

**Verification**:
```sql
-- Check tables exist (23 expected)
SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';

-- Check critical tables
SELECT * FROM pg_tables WHERE tablename IN ('clients', 'deals', 'hosting_subscriptions', 'outreach_metrics', 'system_config');
```

**Files involved**: `scripts/init-db.sql` (V17 schema, 23 tables including clients, deals, hosting_subscriptions, receptionist_subscriptions, outreach_metrics)

### 1.6 Seed Compliance Config

Before any emails can send, the compliance gate (`titan/compliance.py`) requires three config values in the `system_config` table:

```sql
INSERT INTO system_config (key, value) VALUES
  ('company_address', '"Your Physical Address Here"'),
  ('unsubscribe_base_url', '"https://your-domain.com"')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

Also set the `UNSUBSCRIBE_SECRET` env var (used for HMAC-signed unsubscribe links).

**Files involved**: `titan/compliance.py` (ComplianceConfig dataclass, `_normalize_config_str`)

### 1.7 Start All Daemons

**Actions**:
```
make start    # Starts orchestrator, titan, clawdbot, hermes, dashboard, frontend
make status   # Verify all 6 processes show RUNNING
```

**Daemon PID files**: `logs/pids/{orchestrator,titan,clawdbot,hermes,dashboard,frontend}.pid`

**Verification checklist**:
- [ ] `make status` shows all 6 processes RUNNING
- [ ] `make health` shows Docker containers healthy, Ollama responding
- [ ] `scripts/health-check.sh` exits with no FAILURES
- [ ] Hermes dashboard accessible at http://localhost:3000 (or configured port)
- [ ] Telegram bot responds to /status command
- [ ] No ERROR lines in `logs/` within first 15 minutes

### 1.8 48-Hour Stability Test

**Start**: End of Day 8
**End**: End of Day 10
**Method**: Daemons run continuously. `scripts/health-check.sh` runs via cron every 5 minutes.

**Pass criteria**:
- Zero daemon crashes (no PID file resets, no restart needed)
- Zero Docker container restarts (`docker compose ps` shows no restart counts)
- Disk usage stays below 80%
- Ollama responds to every healthcheck
- No OOM kills (check `dmesg | grep -i oom`)

**Monitoring**: Telegram alerts via `scripts/health-check.sh`, Grafana dashboard at http://localhost:3001

**Files involved**: `scripts/health-check.sh`, `scripts/start-perseus.sh`, `scripts/stop-perseus.sh`, Makefile

---

## Task 2: Email Templates (3 Home Services Templates)

**Effort**: 3-4 hours (Day 8-9)
**Owner**: Claude (Titan email_compose stage)
**Dependencies**: Task 1 complete (daemons running, compliance config seeded)

### Context

Titan's email pipeline (`titan/pipeline/email_compose.py`) generates 100% custom emails per lead using Claude API. It loads writing rules from `soul/soul_copy.md`. There are no static templates -- each email is AI-generated from lead research.

However, for the launch we need **three base prompt templates** that guide Titan's email composition for each industry vertical. These are not email templates in the Instantly sense -- they are prompt instructions that tell the LLM how to compose for each vertical.

### 2.1 Plumber Outreach Prompt Template

**File**: `soul/templates/plumber_outreach.md`

**Content requirements**:
- Personalization hooks: reference their Google Maps reviews, service area, specialties (drain cleaning, water heater, etc.)
- Pain points: "customers search 'plumber near me' and find your competitors with websites"
- Value prop: professional 5-page website under $325 with online booking
- Tone: direct, no-nonsense (matches blue-collar audience)
- Under 150 words (per soul_copy.md rules)
- Language detection: English or Spanish based on lead research

**A/B subject line variants**:
- Variant A: "[Business Name] -- your competitors have websites. Do you?"
- Variant B: "I found [Business Name] on Google Maps but not Google Search"

### 2.2 Electrician Outreach Prompt Template

**File**: `soul/templates/electrician_outreach.md`

**Content requirements**:
- Personalization hooks: license number (public record), service area, specialties (residential, commercial, emergency)
- Pain points: "homeowners search online before calling -- if you're not there, they call someone else"
- Value prop: professional website with emergency contact form, service area map
- Tone: professional, trust-oriented (electricians value credentials)
- Under 150 words

**A/B subject line variants**:
- Variant A: "Licensed electricians in [City] deserve a professional website"
- Variant B: "[Business Name] -- I built you a website (free preview)"

### 2.3 General Home Services Prompt Template

**File**: `soul/templates/home_services_outreach.md`

**Content requirements**:
- Covers: HVAC, landscaping, roofing, pest control, cleaning
- Personalization hooks: seasonal relevance (HVAC peaks in summer/winter), service photos from social media
- Pain points: "your best marketing is your reputation -- a website makes it findable"
- Value prop: affordable website that shows up in local search
- Under 150 words

**A/B subject line variants**:
- Variant A: "Quick question about [Business Name]'s online presence"
- Variant B: "[First Name], your [Industry] business deserves more than a Facebook page"

### 2.4 Compliance Validation

Each template must pass `titan/compliance.py` validation:

**Checks**:
- Physical address footer present (uses `{{compliance_footer}}` Instantly variable)
- Unsubscribe link present (HMAC-signed via `UNSUBSCRIBE_SECRET`)
- Subject line accurately represents content (no bait -- per `soul/soul_copy.md` line 38)
- No misleading sender identity

**Verification**:
```python
# Test compliance gate with a sample email
PYTHONPATH=. python3 -c "
import asyncio
from titan.compliance import validate_email_content
result = asyncio.run(validate_email_content(
    subject='Quick question about ABC Plumbing online presence',
    body='...',
    to_email='test@example.com'
))
print('PASS' if result.compliant else f'FAIL: {result.violations}')
"
```

**Files involved**: `soul/soul_copy.md`, `soul/templates/` (new), `titan/pipeline/email_compose.py`, `titan/compliance.py`

---

## Task 3: Lead Pipeline Building (100+ Pre-Qualified Leads)

**Effort**: 6-8 hours (Day 9-12, mostly automated)
**Owner**: Titan pipeline (automated), Nico (quality review)
**Dependencies**: Task 1 complete (Mac Studio stable, Docker + Ollama running)

### 3.1 Configure Lead Discovery for Austin TX

Set target geography and industries in the database config:

```sql
INSERT INTO system_config (key, value) VALUES
  ('discovery_target_city', '"Austin"'),
  ('discovery_target_state', '"TX"'),
  ('discovery_target_country', '"US"'),
  ('discovery_target_industries', '["plumber", "electrician", "hvac", "landscaper"]'),
  ('discovery_batch_size', '20'),
  ('discovery_mode', '"research_only"')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**Files involved**: `titan/pipeline/lead_discovery.py` (reads config via `shared.db.get_config`), `shared/config.py`

### 3.2 Run Discovery in Research-Only Mode

The pipeline has two relevant stages:
1. **Stage 1 -- Lead Discovery** (`titan/pipeline/lead_discovery.py`): Uses skills (Apify, Firecrawl, web search) to find businesses without websites. Inserts into `clients` table with status `discovered`.
2. **Stage 2 -- Lead Research** (`titan/pipeline/lead_research.py`): Deep-researches each discovered lead. Updates status to `researched`.

**Research-only mode** means: run Stage 1 + Stage 2 only. Do NOT proceed to Stage 3 (email_compose) or beyond.

**Actions**:
```python
# Run discovery manually (or let Titan daemon handle it)
PYTHONPATH=. python3 -c "
import asyncio
from titan.pipeline.lead_discovery import discover_leads
from titan.pipeline.lead_research import research_leads

async def run():
    # Discover 5 batches of 20 = 100 leads
    for i in range(5):
        ids = await discover_leads(batch_size=20)
        print(f'Batch {i+1}: discovered {len(ids)} leads')
    # Research all discovered leads
    await research_leads(batch_size=100)

asyncio.run(run())
"
```

**Discovery skills used** (priority order from `lead_discovery.py`):
1. `apify-lead-generation` -- Google Maps, LinkedIn, Instagram scraping
2. `outbound-prospecting` -- Structured lead research workflow
3. `smart-web-scraper` -- Structured data extraction
4. `openclaw-free-web-search` -- Free self-hosted fallback
5. `firecrawl-search` -- Firecrawl-based search

### 3.3 Quality Gate per Lead

Each lead in the `clients` table must meet these criteria before advancing past `researched`:

| Field | Requirement | SQL Check |
|-------|-------------|-----------|
| `business_name` | Non-empty | `business_name != ''` |
| `phone` | Verified (10+ digits) | `length(regexp_replace(phone, '[^0-9]', '', 'g')) >= 10` |
| `industry` | One of target verticals | `industry IN ('plumber', 'electrician', 'hvac', 'landscaper')` |
| `website_url` | NULL or empty (no existing site) | `website_url IS NULL OR website_url = ''` |
| `email` | Valid format | `email ~ '^[^@]+@[^@]+\.[^@]+$'` |
| `status` | Researched with notes | `status = 'researched' AND notes IS NOT NULL` |
| `city` | Austin metro area | From research notes |

**Verification query**:
```sql
SELECT count(*) as total_qualified
FROM clients
WHERE status = 'researched'
  AND business_name != ''
  AND phone IS NOT NULL
  AND length(regexp_replace(phone, '[^0-9]', '', 'g')) >= 10
  AND (website_url IS NULL OR website_url = '')
  AND industry IN ('plumber', 'electrician', 'hvac', 'landscaper');
-- Target: >= 100
```

### 3.4 Lead State Machine Reference

From `titan/state_machine.py`, the valid progression:
```
discovered -> researched -> email_drafted -> email_queued -> email_sent
                                                              -> replied -> interested -> demo_built -> ...
```

For Phase 2, all leads stop at `researched`. Phase 3 (Shadow Mode) will advance them to `email_drafted` for review.

**Files involved**: `titan/pipeline/lead_discovery.py`, `titan/pipeline/lead_research.py`, `titan/state_machine.py`, `shared/db.py`, `scripts/init-db.sql` (clients table schema)

---

## Task 4: Sales Playbook

**Effort**: 3-4 hours (Day 10-11)
**Owner**: Nico
**Dependencies**: None (can run in parallel with Tasks 1-3)

### 4.1 Reply Handling Workflow

**File**: `docs/sales-playbook.md`

Document Nico's workflow for each reply type:

| Reply Type | Action | Response Time | Tool |
|------------|--------|---------------|------|
| Interested ("tell me more") | Send pricing + demo site URL | < 2 hours | Telegram + War Room |
| Price question | Share transparent pricing from soul_copy.md | < 1 hour | Telegram |
| Objection ("too expensive") | Use negotiation template (see 4.2) | < 4 hours | Telegram |
| Objection ("not interested") | Polite close, mark as `lost` | Same day | War Room |
| Spam complaint | Immediately unsubscribe, log incident | < 30 min | War Room + Instantly |
| Wrong person ("I'm not the owner") | Ask for referral to decision maker | Same day | Telegram |

### 4.2 Objection Response Templates

Document template responses for common objections:

1. **"Too expensive"** -- Reference the $299/$325 price point vs. competitor quotes ($2K-5K). Offer payment plan if needed.
2. **"I already have a website"** -- Ask to review it. If it's bad, offer improvement proposal. If it's good, graceful exit.
3. **"I don't need a website"** -- Reference local search stats. Offer to show competitor analysis.
4. **"How do I know this is real?"** -- Share demo site, offer video call, provide business registration.
5. **"I'll think about it"** -- Set specific follow-up date. "Can I check back Thursday?"

### 4.3 Demo Site Process

Document the flow for showing a prospect their preview site:

1. Lead replies with interest (status transitions to `interested` via `titan/state_machine.py`)
2. ClawdBot builds a 1-page demo site (`clawdbot/site_builder.py`)
3. Site deploys to Netlify (`clawdbot/netlify_deploy.py`) with temporary URL
4. Nico sends demo URL to prospect via Telegram relay or email
5. Prospect reviews, provides feedback
6. ClawdBot iterates on feedback (up to 3 rounds)
7. If approved, transition to `proposal_sent` and send invoice

**Files involved**: `clawdbot/site_builder.py`, `clawdbot/netlify_deploy.py`, `titan/state_machine.py`

### 4.4 Stripe Invoice Process (Manual for First 10)

For the first 10 sales, Nico creates invoices manually via Stripe Dashboard:

1. Create customer in Stripe with lead's email and business name
2. Create invoice with line items:
   - 5-page website: $299 one-time
   - Monthly hosting: $52/mo (set up as subscription)
   - Optional: AI receptionist $398/mo (set up as subscription)
3. Send invoice link to prospect
4. On payment, update `deals` table: `status = 'paid'`, `payment_provider = 'stripe'`
5. Trigger site build via War Room or direct command

**After 10 sales**: Switch to automated flow via `tools/payment_router.py` (PaymentRouter class routes to Stripe API).

**Files involved**: `tools/payment_router.py`, `scripts/init-db.sql` (deals table, hosting_subscriptions table)

### 4.5 Pricing Negotiation Guidelines

From `shared/config.py` PricingConfig and `soul/soul_copy.md`:

| Product | List Price | Floor Price | Notes |
|---------|-----------|-------------|-------|
| 5-page website | $299 | $249 | One-time. Cheaper than every competitor. |
| Landing page | $149 | $99 | One-time. For businesses that want minimal presence. |
| Monthly hosting | $52/mo | $42/mo | Includes maintenance, SSL, CDN. |
| AI receptionist | $398/mo | $298/mo | Vendor cost is $79/mo (GoodCall). Margin ~$219-$319. |

**Rules**:
- Never go below floor price
- If prospect wants discount, offer annual prepay (10% off)
- Transparency: show the pricing upfront, never hide fees
- If prospect compares to Wix/Squarespace: emphasize this is done-for-them, not DIY

---

## Task 5: Budget Model

**Effort**: 2 hours (Day 11)
**Owner**: Nico
**Dependencies**: Task 1 (budget_guard.py needs running system)

### 5.1 Monthly Cost Breakdown

**File**: `docs/budget-model.md`

| Category | Service | Monthly Cost | Notes |
|----------|---------|-------------|-------|
| AI | Claude API (Anthropic) | $200-400 | ~$0.01/email, ~$0.50/site build, scales with volume |
| AI | Ollama (local) | $0 | Runs on Mac Studio, electricity only |
| Email | Instantly.ai | $97 | Growth plan, unlimited sending accounts |
| Scraping | Firecrawl | $16 | Hobby plan, 500 credits/mo |
| Hosting | Netlify | $0-19 | Free tier covers first ~20 sites, Pro at $19/mo |
| Payments | Stripe | ~3% of revenue | Per-transaction, no monthly fee |
| Infrastructure | Docker (local) | $0 | Runs on Mac Studio |
| Infrastructure | Domain (1) | ~$12/year ($1/mo) | For unsubscribe endpoint and company site |
| Monitoring | Grafana/Prometheus | $0 | Self-hosted |
| **Total fixed** | | **~$314-514/mo** | |

### 5.2 Unit Economics

| Metric | Value |
|--------|-------|
| Revenue per website sale | $299 |
| Revenue per hosting subscriber | $52/mo |
| Revenue per receptionist subscriber | $398/mo (margin: $319/mo after GoodCall) |
| Cost per lead (discovery + research) | ~$0.05 (Firecrawl + Ollama) |
| Cost per email sent | ~$0.01 (Claude API) |
| Cost per site built | ~$0.50 (Claude API + Recraft images) |
| Break-even customers (website only) | ~2/month at $299 each |
| Break-even customers (website + hosting) | 1 website + 6 hosting subs covers $314 fixed |

### 5.3 Budget Guard Configuration

Configure alerts in `tools/budget_guard.py` via env vars and database config:

```
MONTHLY_BUDGET_CAP=800          # Hard cap (already default in shared/config.py)
BUDGET_ALERT_THRESHOLD=0.80     # Alert at 80% spend ($640)
```

**Optional per-category caps** (set via `system_config` table):
```sql
INSERT INTO system_config (key, value) VALUES
  ('budget_cap_claude_api', '400'),
  ('budget_cap_instantly', '100'),
  ('budget_cap_firecrawl', '20'),
  ('budget_cap_cloud_gpu', '200')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**Verification**: `BudgetGuard.check_budget()` returns current spending and remaining budget. Alerts fire via Telegram when 80% threshold is crossed.

**Files involved**: `tools/budget_guard.py` (BudgetGuard class), `shared/config.py` (BudgetConfig), `shared/db.py`

---

## Task 6: Kill Switch Documentation

**Effort**: 1-2 hours (Day 12)
**Owner**: Nico
**Dependencies**: None

### 6.1 Instantly Pause Procedure

**File**: `docs/kill-switches.md`

**Immediate pause** (stops all outgoing emails within minutes):

1. **Via Instantly Dashboard**: Login > Campaigns > Select campaign > Pause
2. **Via API**: Call `InstantlyClient.pause_campaign(campaign_id)` from `tools/instantly_client.py`
3. **Via Perseus**: `make stop` kills Titan daemon, preventing new emails from being queued
4. **Nuclear option**: Set `discovery_mode` to `"paused"` in system_config:
   ```sql
   UPDATE system_config SET value = '"paused"' WHERE key = 'discovery_mode';
   ```

**Resume procedure**: Reverse the steps. Verify warmup reputation hasn't degraded in Instantly dashboard before resuming.

### 6.2 Domain Blacklist Response Plan

If a sending domain gets blacklisted (detected via Instantly deliverability dashboard or MXToolbox):

| Severity | Signal | Action | Timeline |
|----------|--------|--------|----------|
| Warning | Open rates drop below 20% | Reduce daily volume by 50% | Same day |
| Moderate | Domain listed on 1 blacklist | Pause that domain, switch to backup | Same day |
| Severe | Domain listed on 2+ blacklists | Pause ALL sending, investigate | Immediate |
| Critical | Spam complaint from recipient | Unsubscribe immediately, pause campaign | < 30 min |

**Blacklist removal steps**:
1. Identify which blacklist(s) via MXToolbox
2. Submit removal request (most auto-expire in 7-14 days)
3. Review email content for spam triggers
4. Warm up a new domain as backup (takes 14-21 days)

### 6.3 Legal Threat Escalation

If a recipient sends a legal threat (cease and desist, CAN-SPAM complaint, etc.):

| Step | Action | Owner |
|------|--------|-------|
| 1 | Immediately unsubscribe the sender from all campaigns | Nico (< 1 hour) |
| 2 | Save the complaint email in full (screenshot + raw) | Nico |
| 3 | Pause all campaigns to the same industry/region temporarily | Nico |
| 4 | Review compliance: was the unsubscribe link present? Physical address? | Nico |
| 5 | If compliant: respond professionally acknowledging removal | Nico (< 24 hours) |
| 6 | If non-compliant: fix the compliance gap, document root cause | Nico + Claude |
| 7 | If formal legal action: consult attorney | Nico (escalation) |

**Prevention**: `titan/compliance.py` enforces CAN-SPAM requirements (physical address, unsubscribe link, no bait subject lines). Every email goes through `send_to_instantly()` -- the single compliance choke point.

**Files involved**: `docs/kill-switches.md` (new), `tools/instantly_client.py`, `titan/compliance.py`

---

## Verification & Success Criteria

### Final Checklist (Day 14)

| # | Criterion | How to Verify | Status |
|---|-----------|---------------|--------|
| 1 | All 5 daemons stable on Mac Studio for 48+ hours | `make status` + zero crashes in health-check logs | [ ] |
| 2 | All Docker containers healthy | `docker compose ps` shows all healthy, zero restarts | [ ] |
| 3 | Ollama serving all 3 models | `curl localhost:11434/api/tags` lists 3 models | [ ] |
| 4 | 3 email prompt templates created | Files exist in `soul/templates/` | [ ] |
| 5 | Templates pass compliance validation | `titan/compliance.py` returns compliant=True for sample emails | [ ] |
| 6 | 100+ pre-qualified leads in clients table | SQL count query returns >= 100 with status='researched' | [ ] |
| 7 | Each lead has verified phone + no existing website | Quality gate SQL query passes | [ ] |
| 8 | Sales playbook document completed | `docs/sales-playbook.md` exists with all sections | [ ] |
| 9 | Budget model shows positive unit economics | `docs/budget-model.md` shows break-even at ~2 sales/month | [ ] |
| 10 | Budget alerts configured | `BudgetGuard.check_budget()` returns valid response | [ ] |
| 11 | Kill switch docs completed | `docs/kill-switches.md` exists with all procedures | [ ] |
| 12 | Telegram alerts working | Health check failure triggers Telegram message | [ ] |

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Mac Studio arrives late | Low | High (blocks everything) | Continue development on current machine |
| Ollama OOM on 32GB | Medium | Medium | Use TurboQuant KV cache (config: `OLLAMA_KV_CACHE_TYPE=turbo4`), reduce batch sizes |
| Firecrawl quota exhaustion | Medium | Low | Fall back to `openclaw-free-web-search` skill |
| Lead discovery returns < 100 leads for Austin | Low | Medium | Expand radius to San Antonio, Dallas metros |
| Docker service instability | Low | Medium | Memory limits set in docker-compose.yaml, health checks every 10-15s |

### Handoff to Phase 3

Phase 2 produces:
- Running Mac Studio with all services stable
- 100+ researched leads ready for email composition
- Email prompt templates validated against compliance
- Sales playbook and pricing ready for first conversations
- Budget monitoring active with Telegram alerts
- Kill switch procedures documented and tested

Phase 3 (Shadow Mode Dry Run, Days 14-19) will:
- Advance leads from `researched` to `email_drafted`
- Run full pipeline end-to-end without external sends
- Test War Room approval flow
- Validate site building with ClawdBot
