> **Historical**: This handoff reflects the March 2026 audit/fixer cycle state. See the completion sweep for current status.

# PERSEUS v18 — Context Handoff (March 21, 2026)

## What This Build Is

4-daemon autonomous website-sales system running natively on Mac M4 32GB. Claude-first, Ollama fallback. Postgres task queue for coordination, Mem0 for shared vector memory.

### Architecture:
```
PERSEUS (master scheduler)  → inserts tasks into task_queue every 10 seconds
TITAN   (revenue engine)    → polls task_queue + runs 10-stage pipeline continuously
HERMES  (Telegram + alerts) → dispatches events to Nico, serves web dashboard
CLAWDBOT (skills + browser) → executes skills, scrapes sites, verifies deployments, enriches leads
```

All 4 daemons share state through: `task_queue`, `events`, `titan_learnings`, `system_config` (Postgres) and Mem0 vector store. Inter-daemon API in `shared/comms.py`.

### What Was Built:

**Session 1 (March 20):** Complete restructure from 30 CrewAI micro-agents to 3-daemon architecture. 7 commits. Built full 10-stage pipeline, skill system, learning/training pipeline, review mode.

**Session 2 (March 21):** Fixed all broken wiring, added 4th daemon, made repo self-contained.

1. Created `tools/runtime_honesty.py` — unblocked Firecrawl imports
2. Created `tools/payment_router.py` — unblocked invoice pipeline (Stripe + Wise)
3. Fixed lead discovery — stores leads without email (enriched in research stage)
4. Fixed `.env.example` — SMARTLEAD_API_KEY → INSTANTLY_API_KEY
5. Fixed proposal sending — uses Instantly campaign `add_lead()`, not missing `send_email()`
6. Fixed proposal pipeline — only transitions to `proposal_sent` if send succeeds
7. Fixed invoice pipeline — only creates deal record after payment reference confirmed
8. Fixed analytics schema — correct column names for `outreach_metrics` table
9. Added Titan handlers for `health_check`, `budget_check`, `morning_briefing`
10. Wired Hermes morning briefing dispatch
11. Added `instantly_id` column to `email_sequences`
12. Created ClawdBot daemon — skills, scraping, site verification, lead enrichment
13. Created `shared/comms.py` — inter-daemon messaging, shared memory, agent status
14. Added `requests` to requirements.txt (Firecrawl dependency)
15. Fixed all ops scripts — stop, Makefile, LaunchAgents all manage 4 daemons
16. Fixed LaunchAgent plists — point to `/Users/majovega/Desktop/objective-hertz`
17. Initialized standalone git repo (was stale worktree pointer)
18. Updated README.md and HANDOFF.md to match actual code

### Key Decisions:
- v0.dev API for site building (Bolt.new has no public API)
- Instantly.ai for email ($97/mo, campaign-based, best API)
- Stripe Mexico (primary) + Wise Business (fallback) for payments
- $800/month budget ($200 Claude Max + tools)
- Review mode for first 10 sales, then full autonomy
- Skills-first pipeline: try installed skills before custom code
- LoRA training at 500+ labeled examples (Vast.ai GPU or local MLX)
- ClawdBot handles all skill execution and browser automation

### What's NOT done (needs Nico):
1. Sign up for Instantly.ai ($97/mo)
2. Sign up for v0.dev API (free tier)
3. Set up Stripe Mexico or Wise Business
4. Buy 4 sending domains
5. Create Telegram bot via @BotFather
6. Get Anthropic API key (or Claude Max subscription)
7. Fill `.env` with real values
8. `make start` on Mac M4

### Key Files:
- `shared/config.py` — All config from .env (frozen dataclasses)
- `shared/llm_client.py` — Claude API + Ollama (fast/smart/local modes)
- `shared/db.py` — Postgres async pool (psycopg v3)
- `shared/comms.py` — Inter-daemon communication layer
- `shared/skill_loader.py` — Finds and executes skills from 4 directories
- `perseus/daemon.py` — Master scheduler (15 schedules, 10-second tick)
- `perseus/scheduler.py` — Schedule definitions
- `titan/daemon.py` — Pipeline loop (task queue + 9-stage cycle)
- `titan/pipeline/*.py` — 10 stages (discovery through invoice)
- `titan/memory.py` — Daily/weekly learning + Mem0 vector memory
- `titan/training.py` — LoRA fine-tuning pipeline (collect → export → train → deploy)
- `titan/state_machine.py` — Lead status transitions (17 states)
- `titan/review_mode.py` — First 10 sales approval queue
- `hermes/telegram_bot.py` — 9 Telegram commands
- `hermes/alerts.py` — Event → Telegram dispatcher + morning briefing
- `hermes/web/app.py` — FastAPI API backend (port 8500, token auth)
- `hermes/web/frontend/` — War Room React dashboard (Next.js, port 3000)
- `clawdbot/daemon.py` — Autonomous skills agent (26 capabilities, self-equipping)
- `clawdbot/capabilities.py` — Capability map (3 registries, Claw ecosystem)
- `clawdbot/capability_resolver.py` — Auto-install missing skills from registries
- `clawdbot/safety.py` — Skill vetting gate (static scan + LLM review)
- `titan/deliverability.py` — Domain protection + warm-up ramp + volume control
- `titan/expansion.py` — Revenue expansion gate with shadow rollouts
- `tools/instantly_client.py` — Instantly.ai API v2 client
- `tools/payment_router.py` — Stripe + Wise payment routing
- `tools/firecrawl_client.py` — Web scraping with runtime honesty
- `tools/budget_guard.py` — $800/month budget enforcement
- `scripts/init-db.sql` — Full schema (20+ tables, views, indexes)

### Known Limitations:
- `deploy_site.py` only verifies — actual deployment handled by `build_site.py` via v0.dev
- No end-to-end test yet (needs running infrastructure + real API keys)
- Vast.ai automation requires SSH key setup for SCP uploads
- N8N is provisioned in Docker but not actively used by the Python runtime
- ClawdBot browser skills resolve via DroidClaw/agent-browser/playwright — falls back to Firecrawl HTTP scraping when none are installed
