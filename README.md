# Objective Hertz

Fully autonomous AI revenue system. Finds businesses without websites globally, emails them custom outreach, follows up, closes, builds websites, delivers, invoices, and learns from every interaction. Runs 24/7 on Mac M4 32GB.

## Architecture

OpenJarvis is the orchestrator framework. Five daemons run under it:

- **Perseus** — Master scheduler. Inserts tasks into the pipeline queue, monitors health, enforces budget.
- **Titan** — Revenue engine. 10-stage pipeline: discover → research → email → follow up → demo → close → build → deploy → invoice → learn.
- **Hermes** — Alerts and dashboard. Dispatches Telegram notifications, serves the War Room web UI, exposes the FastAPI API.
- **ClawdBot** — Site builder and browser automation. Builds sites using Recraft for AI-generated images, deploys to Netlify, runs scraping and verification skills.
- **Conway** — Economics daemon. Manages agent wallets (Base L2 USDC), x402 micropayments, survival-tier enforcement, team collaboration via `ask_agent` / `delegate`.

All daemons communicate via A2A (agent-to-agent), Postgres (shared task queue + 23 tables), and Mem0 vector memory. See `shared/comms.py`.

## Quick Start

```bash
cp .env.example .env          # Configure API keys
pip install -r requirements.txt
make up                       # Start Docker (Postgres, Qdrant, Mem0, N8N)
make start                    # Start Hermes gateway + workers + dashboard + sidecars
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
| 5-page website | $299 | AI-built via ClawdBot + Recraft |
| Landing page | $149 | Single-page variant |
| Hosting | $52/mo | Managed via Netlify |
| AI receptionist | $398/mo | Optional recurring upsell |

## Budget: $800/month

$200 Claude Max + ~$100 Instantly.ai + tools/domains + GPU training buffer.

## Stack

| Component | Tool |
|-----------|------|
| AI Brain | Hermes Agent (primary interface) + provider-selected main model |
| Pipeline | Titan (Python asyncio, 10 stages) |
| Interface | War Room (React :3000) + Telegram bot + FastAPI API (:8500) |
| Skills | ClawdBot (26 capabilities, 3 registries, safety vetting) |
| Email | Instantly.ai (campaign-based, warmup, account rotation) |
| Scraping | Firecrawl + browser-use |
| Heavy Local Research | AirLLM (optional, long-context offline analysis and memory digestion) |
| Sites | ClawdBot (site_builder.py) + Recraft (images) + Netlify (deploy) |
| Payments | Stripe (primary) + Wise (fallback) |
| DB | Postgres + Qdrant + Mem0 |
| Orchestration | OpenJarvis framework + Python daemons + Postgres task queue |

## Commands

```bash
make setup     # First-run: prompts for API keys, generates secrets, builds frontend
make start     # Start everything (Docker + Hermes gateway + workers + War Room + sidecars)
make stop      # Stop everything
make status    # System status (Hermes gateway + workers + Docker + Ollama + sidecars)
make dashboard # Start dashboard backend standalone (dev mode, port 8500)
make logs      # Tail all daemon logs
make health    # Quick health check (Postgres, Qdrant, Mem0, N8N, Ollama, sidecars)
make restart   # Stop + start
make up        # Docker services only
make down     # Stop Docker
```

## 24/7 Operation (macOS LaunchAgents)

```bash
./scripts/install-launchagents.sh   # Install the LaunchAgent plists
# Auto-starts on login, auto-restarts on crash
```

## Directory Structure

```
openjarvis/       Orchestrator framework (A2A, agents, WorkflowEngine, security, tools)
shared/           LLM client (Claude+Ollama+AirLLM policy), DB pool, comms layer, skill loader, MAGMA
perseus/          Scheduler daemon, agent registry, health, self-audit
titan/            Pipeline (10 stages), state machine, memory, training, review mode
hermes/           Telegram alerts, FastAPI API, War Room web UI
clawdbot/         Site builder, Recraft images, Netlify deploy, browser scraping
conway/           Agent wallets (Base L2 USDC), x402 payments, survival tiers
tools/            Instantly, Firecrawl, Recraft, payments, budget guard
soul/             Personality, autonomy rules, copywriting guidelines
scripts/          Start/stop, install scripts, LaunchAgent plists, DB schema
templates/        Industry website templates (dentist, plumber, restaurant)
orchestrator.py   Top-level entry point (replaces Perseus daemon for local runs)
```
