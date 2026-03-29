> **Historical**: This roadmap reflects early March 2026 planning. Current project state is tracked in the completion sweep.

# PERSEUS ROADMAP — Single Source of Truth

**Last updated:** March 21, 2026
**Goal:** $2,000/month recurring by July 2026 (Month 4)
**Owner:** Nico

---

## What Perseus IS (right now)

An autonomous website-sales system written in Python, designed to run 24/7 on a Mac M4 32GB. It finds businesses without websites, emails them, follows up, builds sites, delivers, invoices, and learns. Four daemons coordinate through Postgres and vector memory.

**It is NOT yet the agent-of-agents system you originally envisioned.** That vision (a master agent using Claude Code, OpenClaw, and Hermes as tools) is still a valid long-term direction, but the current build is a Python automation system with LLM calls. That's fine for now — it can make money as-is once it's running.

## The Math

| Service | Price | Sales needed for $2K/mo |
|---------|-------|------------------------|
| 5-page website | $299 | 7 per month |
| Landing page | $149 | 14 per month |
| Hosting upsell | $52/mo | Recurring (compounds) |
| AI receptionist | $398/mo | Recurring (compounds) |

**Realistic target:** 5 website sales + 2-3 hosting clients/month = ~$1,600-$1,800 base, growing with recurring revenue. By month 4, hosting revenue from months 1-3 pushes you past $2K.

## Current State — What's Done vs. What's Not

### DONE (keep it all)

- 4-daemon architecture (Perseus, Titan, Hermes, ClawdBot)
- 10-stage pipeline logic (discovery → invoice)
- Postgres schema (20+ tables)
- LLM routing (Claude + Ollama)
- State machine (17 lead states)
- Budget guard ($800/mo cap)
- Compliance gates (CAN-SPAM, unsubscribe)
- Review mode (first 10 sales need your approval)
- Deliverability protection (warm-up, volume ramp)
- React dashboard (War Room)
- Telegram bot (9 commands)
- Test suite (exists, needs passing)
- LaunchAgent plists for macOS auto-start

### NOT DONE (the blockers)

1. **No API keys configured** — .env is empty/placeholder
2. **No Instantly.ai account** — can't send emails
3. **No payment processor** — can't invoice
4. **No sending domains** — can't do outreach
5. **No Telegram bot** — can't get alerts
6. **No v0.dev access** — can't build sites
7. **No end-to-end test** — never run the full pipeline once
8. **Vendored dependencies are heavy** — browser-use (200+ files) and firecrawl SDK are cloned into the repo

### DRIFT / BLOAT (acknowledge but don't delete yet)

- `perseus/backprop.py`, `perseus/cell_division.py`, `perseus/sleep_cycle.py` — ambitious self-improvement features, not needed for v1 revenue
- `shared/self_model.py`, `shared/execution_loop.py` — same category
- `tools/browser-use/` — entire vendored library (200+ files), only needed if ClawdBot does browser automation
- `tools/firecrawl/` — entire vendored SDK, could be a pip install instead
- `tools/notebooklm_client.py`, `tools/recraft_client.py` — nice-to-have, not revenue-critical
- `clawdbot/` — the whole 4th daemon is forward-looking; Titan can handle the first sales without it

---

## THE ROADMAP

### Phase 0: Foundation (Week 1) — "Can it send one email?"

**Goal:** Send one real cold email through the pipeline and receive it in a test inbox.

**Steps:**

1. Sign up for Instantly.ai Hypergrowth ($97/mo)
2. Buy 2 sending domains (~$20 total, e.g. from Namecheap/Porkbun)
3. Set up DNS (SPF, DKIM, DMARC) for sending domains
4. Start Instantly warm-up (takes 2-3 weeks to reach full volume)
5. Create a Telegram bot via @BotFather
6. Get Anthropic API key (or confirm Claude Max provides API access)
7. Fill `.env` with real values
8. Start Docker services (`make up` — Postgres, Qdrant, Mem0)
9. Run `scripts/init-db.sql` to create tables
10. Run one pipeline cycle manually: discover a lead → research it → compose an email → send it via Instantly
11. Verify: email lands in test inbox, lead status updated in DB, Telegram notification received

**Success = one email sent and tracked.** Nothing else matters until this works.

**Cost:** ~$120 (Instantly + domains). No other tools needed yet.

### Phase 1: First Outreach (Weeks 2-4) — "Can it fill a pipeline?"

**Goal:** 50-100 leads discovered, 20-30 emails sent (warm-up limited), first replies coming in.

**Steps:**

1. Tune lead discovery for ONE niche (pick the easiest: restaurants, dentists, or plumbers in US/Mexico small towns)
2. Write 3 email templates, A/B test via Instantly campaigns
3. Let Titan run on a 1-hour cycle (not 10-second) to keep costs low
4. Monitor via Telegram + dashboard
5. Manually handle any replies (don't automate follow-up yet)
6. Track: open rates, reply rates, which niches respond

**You are doing sales manually at this point.** Perseus finds leads and sends first touch. You close.

**Success = 3+ warm replies from businesses interested in a website.**

### Phase 2: First Revenue (Weeks 4-8) — "Can it make money?"

**Goal:** Close first 2-3 website sales. Deliver manually if needed.

**Steps:**

1. Set up Stripe Mexico (or Wise Business) — you need to receive money
2. Set up v0.dev API access
3. Test site building: use v0 to generate a site for one of your template industries
4. Price it at $299 for 5-page site, $149 for landing page
5. Close deals from Phase 1 replies — you handle calls/demos, Perseus handles the pipeline
6. Build and deliver 2-3 sites (semi-manually using v0 + your review)
7. First 10 sales go through review mode — you approve every step

**Success = $500-$900 in revenue. Proof the business model works.**

### Phase 3: Automation (Weeks 8-12) — "Can it run without me?"

**Goal:** Titan handles discovery → email → follow-up → site build → delivery → invoice with minimal intervention. You review deals, not individual emails.

**Steps:**

1. Turn on automated follow-up sequences (3-touch cadence)
2. Automate site building pipeline (v0 API → deploy → send preview link)
3. Automate invoicing (Stripe payment links)
4. Ramp email volume (warm-up should be complete by now)
5. Expand to 2-3 niches
6. Add hosting upsell to delivery flow ($52/mo recurring)
7. Increase Titan cycle frequency as you trust it more
8. Start logging learnings to `titan_learnings` table

**Success = $1,500-$2,000/month with <1 hour/day of your time.**

### Phase 4: Scale + Original Vision (Month 4+) — "Agent of agents"

**Only after revenue is flowing.** This is where the original Perseus vision comes back:

- Introduce ClawdBot for heavier tasks (competitor research, portfolio sites, enrichment)
- Explore using Claude Code as an agent that Perseus can delegate to
- Look at OpenClaw/Hermes integration for the meta-agent layer
- Self-improvement features (backprop, cell division, sleep cycle) become relevant
- LoRA training on your own sales data
- Consider SaaS-ifying Perseus itself

---

## WHAT TO IGNORE UNTIL PHASE 3

These are real features in the codebase that are premature:

- ClawdBot daemon (whole thing)
- LoRA training pipeline
- Self-model / backprop / cell division / sleep cycle
- Browser-use automation
- N8N workflows
- Revenue expansion engine
- NotebookLM integration
- Recraft client

They're not bad ideas. They're just not what makes the first dollar.

## CODEBASE ORGANIZATION

The repo stays as-is. No deletion. But here's what matters *right now*:

```
CRITICAL PATH (touch these first):
  shared/config.py          — Get .env loaded
  shared/db.py              — Connect to Postgres
  shared/llm_client.py      — Claude API calls working
  titan/pipeline/lead_discovery.py  — Find businesses
  titan/pipeline/lead_research.py   — Qualify them
  titan/pipeline/email_compose.py   — Write the email
  titan/pipeline/email_send.py      — Send via Instantly
  tools/instantly_client.py         — Instantly API wrapper
  scripts/init-db.sql               — Database schema

IMPORTANT BUT LATER:
  titan/pipeline/follow_up.py
  titan/pipeline/build_site.py
  titan/pipeline/deploy_site.py
  titan/pipeline/close_deal.py
  titan/pipeline/invoice.py
  tools/payment_router.py
  hermes/telegram_bot.py
  hermes/alerts.py

CAN WAIT:
  Everything else
```

## MONTHLY BUDGET

| Item | Cost | When needed |
|------|------|-------------|
| Instantly.ai | $97/mo | Phase 0 (now) |
| Sending domains (2) | $20 one-time | Phase 0 (now) |
| Anthropic API | $0-200/mo | Phase 0 (Claude Max or API) |
| v0.dev | $0-10/mo | Phase 2 |
| Stripe Mexico | 3.6% per txn | Phase 2 |
| Docker (local) | $0 | Already running |
| **Total Phase 0-1** | **~$120-320/mo** | |

## DECISION LOG

| Date | Decision | Rationale |
|------|----------|-----------|
| March 2026 | Instantly.ai over Smartlead | Best API, unlimited accounts, $97/mo |
| March 2026 | v0.dev over Bolt.new | Only one with public API |
| March 2026 | Stripe MX + Wise | Cover cards + wire transfers |
| March 2026 | Phase approach over big-bang | Must prove revenue before building agent-of-agents |
| March 2026 | Keep all code, focus on critical path | Don't lose work, but stop working on non-revenue features |

---

## ONE RULE TO PREVENT FUTURE DRIFT

**Before working on ANY feature, ask: "Does this help send the next email or close the next sale?"**

If no → put it in a `FUTURE.md` file and move on.
If yes → do it.

This rule expires once you hit $2K/month sustained. Then you can build the agent-of-agents dream.
