# PERSEUS

Fully autonomous AI revenue system. Finds businesses without websites globally, emails them custom outreach, follows up, closes, builds websites, delivers, invoices, and learns from every interaction. Runs 24/7 on Mac M4 32GB.

## Architecture

```
                    PERSEUS (Master Scheduler)
                   /       |        \         \
              TITAN    HERMES    CLAWDBOT    (Postgres + Mem0)
           (revenue) (interface) (skills)     shared memory
```

4 daemons, 1 shared brain:

- **Perseus** — Master orchestrator. Schedules work, monitors health, enforces budget.
- **Titan** — Revenue engine. 10-stage pipeline: discover → research → email → follow up → demo → close → build → deploy → invoice → learn.
- **Hermes** — Nico's interface. Telegram bot, event alerts, web dashboard, morning briefings.
- **ClawdBot** — Skills executor. Runs installed skills, scrapes websites, verifies deployments, enriches leads.

All 4 daemons communicate through Postgres (task_queue, events, titan_learnings, system_config) and Mem0 vector memory. See `shared/comms.py`.

## Quick Start

```bash
cp .env.example .env          # Configure API keys
pip install -r requirements.txt
make up                       # Start Docker (Postgres, Qdrant, Mem0, N8N)
make start                    # Start all 4 daemons
```

## Autonomy Rules

Perseus decides everything except:
1. Recurring cost >$50/mo or one-time >$200
2. High-risk system changes (schema, LoRA training)
3. Enterprise deals ($50K+)
4. First 10 sales (Nico approves, then full autonomy)

## Revenue-Driven Expansion

Perseus only expands itself when expansion is tied to money.

- Detect a real bottleneck hurting revenue, margin, or throughput
- Propose the smallest capability that could fix it
- Gate it on projected ROI and monthly experiment budget
- Shadow-test it on a slice of traffic first
- Keep it only if the shadow beats the baseline

In this build, the first live rollout surface is lead discovery skills. New skill opportunities are tracked in `revenue_expansion_opportunities`, and discovery skills can be shadowed/adopted automatically when they outperform the baseline.

For bigger expansions:
- `tool` and `agent` opportunities do not self-edit the repo directly from Titan
- they must go through ClawdBot using installed OpenClaw/Codex/Claude-compatible builder skills
- Titan scores the opportunity, ClawdBot produces the build blueprint

## Services

| Service | Price | Notes |
|---------|-------|-------|
| 5-page website | $299 | AI-built via v0.dev API |
| Landing page | $149 | Single-page variant |
| Hosting | $52/mo | Managed via Netlify/Vercel |
| AI receptionist | $398/mo | Optional recurring upsell |

## Budget: $800/month

$200 Claude Max + ~$100 Instantly.ai + tools/domains + GPU training buffer.

## Stack

| Component | Tool |
|-----------|------|
| AI Brain | Claude API (primary) + Ollama Qwen2.5 14B (fallback) |
| Pipeline | Titan (Python asyncio, 10 stages) |
| Interface | Hermes Telegram bot + web dashboard (port 8500) |
| Skills | ClawdBot + shared/skill_loader.py (4 skill directories) |
| Email | Instantly.ai (campaign-based, warmup, account rotation) |
| Scraping | Firecrawl + browser-use |
| Sites | v0.dev Platform API (project → chat → deploy) |
| Payments | Stripe (primary) + Wise (fallback) |
| DB | Postgres + Qdrant + Mem0 |
| Orchestration | Python daemons + Postgres task queue |

## Commands

```bash
make start    # Start everything (Docker + 4 daemons)
make stop     # Stop everything
make status   # System status (all 4 daemons + Docker + Ollama)
make logs     # Tail all daemon logs
make health   # Quick health check (Postgres, Qdrant, Mem0, N8N, Ollama)
make restart  # Stop + start
make up       # Docker services only
make down     # Stop Docker
```

## 24/7 Operation (macOS LaunchAgents)

```bash
./scripts/install-launchagents.sh   # Install all 4 LaunchAgent plists
# Auto-starts on login, auto-restarts on crash
```

## Directory Structure

```
shared/           Config, LLM client (Claude+Ollama), DB pool, comms layer, skill loader
perseus/          Master daemon, scheduler (15 schedules), agent registry
titan/            Pipeline (10 stages), state machine, memory, training, review mode
hermes/           Telegram bot (9 commands), alerts, web dashboard, Hermes skills
clawdbot/         Skills executor, web scraper, site verifier, lead enricher
tools/            Instantly, Firecrawl, payments, budget guard, runtime honesty
soul/             Personality, autonomy rules, copywriting guidelines
scripts/          Start/stop, install scripts, LaunchAgent plists, DB schema
templates/        Industry website templates (dentist, plumber, restaurant)
.agent/skills/    Installed skill collections (marketing, firecrawl, web-scraper, voltagent)
```
