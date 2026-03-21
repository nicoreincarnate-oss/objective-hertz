# PERSEUS — Context Handoff (March 20, 2026)

## What Was Done This Session

Complete restructure of Perseus from 30 CrewAI micro-agents to 3 real autonomous agents. 7 commits on branch `claude/objective-hertz`.

### Commits (in order):
1. **Complete restructure** — Deleted 956K lines of old CrewAI system. Built new architecture: shared/ (6 files), perseus/ (3 files), titan/ (13 files), hermes/ (8 files). Full 10-stage pipeline.
2. **Remaining gaps** — Updated all soul files, wired Titan↔Perseus task queue, research findings (Instantly.ai, Stripe Mexico, v0.dev API), updated all docs, created install scripts.
3. **Skill system** — Built skill_loader.py, refactored pipeline to be skill-first (try installed skills before custom code), installed 4 skill collections (marketing, firecrawl, web-scraper, voltagent).
4. **Bug fixes** — Fixed all 19 broken transition_lead() calls, lambda task handlers, JSON double-encoding, titan_builder API mismatch, Hermes bot lifecycle.
5. **Learning system** — Built training.py (LoRA pipeline: collect→export→train→deploy), upgraded memory.py (vector memory via Mem0, structured learnings, feedback loop).
6. **Wired learning feedback loop** — Training data collection wired into follow_up.py (collect_email_outcome on replies), email_send.py (records every sent email as training example), close_deal.py (marks all emails in closed deal as positive). get_relevant_learnings() (DB + Mem0 vector search) wired into email_compose.py, lead_discovery.py, follow_up.py compose, and close_deal.py proposal generation.
7. **Full Vast.ai automation** — Replaced stub with complete lifecycle: search cheapest GPU → rent instance → wait for startup → SCP upload data + training script → run Unsloth LoRA fine-tuning → poll for completion → download adapter → destroy instance. Falls back to local MLX training. Budget-capped at $100/run.

### Architecture:
```
PERSEUS (master scheduler) → inserts tasks into task_queue
TITAN (revenue daemon) → polls task_queue + runs 10-stage pipeline
HERMES (Telegram bot + web dashboard) → alerts, commands, approvals
OPENCLAW (desktop agent) → skills, browser control (install script ready)
```

### Master Plan: `/Users/majovega/.claude/plans/joyful-exploring-hearth.md`
- Phases 1-7: DONE (code complete)
- Phase 8 (email infrastructure): Code done, needs account signups

### What's NOT done (needs Nico):
1. Sign up for Instantly.ai ($97/mo) or Smartlead
2. Sign up for v0.dev API (free tier)
3. Set up Stripe Mexico or Wise Business
4. Buy 4 sending domains
5. Create Telegram bot (@BotFather)
6. Get Anthropic API key
7. Fill .env with real values
8. Install Hermes Agent: `./scripts/install-hermes.sh`
9. Install OpenClaw from DMG + run `./scripts/install-openclaw.sh`
10. Install skills: `./scripts/install-skills.sh`
11. `make start` on Mac Studio

### Key Files:
- `shared/config.py` — All config from .env
- `shared/llm_client.py` — Claude API + Ollama (fast/smart/local modes)
- `shared/db.py` — Postgres async pool + helpers
- `shared/skill_loader.py` — Finds and executes installed skills
- `perseus/daemon.py` — Master scheduler (13 schedules)
- `titan/daemon.py` — Pipeline loop (polls task_queue + runs stages)
- `titan/pipeline/*.py` — 10 stages (discovery through invoice)
- `titan/memory.py` — Daily/weekly learning + vector memory (Mem0)
- `titan/training.py` — LoRA fine-tuning pipeline (collect→train→deploy)
- `titan/state_machine.py` — Lead status transitions
- `titan/review_mode.py` — First 10 sales approval queue
- `hermes/telegram_bot.py` — 9 Telegram commands
- `hermes/alerts.py` — Event→Telegram dispatcher
- `hermes/web/app.py` — FastAPI dashboard
- `tools/instantly_client.py` — Instantly.ai API client
- `scripts/init-db.sql` — Full schema (15+ tables)

### Key Decisions (from 30-question survey):
- Global market, any industry, AI picks targets
- Under $325 for 5-page website, cheaper than market
- v0.dev API for site building (Bolt.new has NO public API)
- Instantly.ai for email ($97/mo, best API + webhooks)
- Stripe Mexico + Wise Business for payments
- $800/month budget ($200 Claude Max + tools)
- Review mode for first 10 sales, then full autonomy
- Skills-first: use existing open-source skills before custom code
- LoRA training when 500+ labeled examples exist (cloud GPU or local MLX)

### Known Issues:
- deploy_site.py only verifies, doesn't do actual deployment (handled by build_site.py)
- Some tools/*.py files (smartlead_client, firecrawl_client, etc.) are from old V17 and may need API updates
- No end-to-end test yet (needs running infrastructure)
- Vast.ai automation requires SSH key setup on Mac Studio (for SCP uploads to rented instances)
