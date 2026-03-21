# PERSEUS

Fully autonomous AI revenue system. Finds businesses without websites globally, emails them custom outreach, follows up, closes, builds websites, delivers, invoices, and learns from every interaction. Runs 24/7 on Mac Studio M4 Max 32GB.

## Architecture

```
                    PERSEUS (Master Brain)
                   /       |        \
              TITAN    HERMES    OPENCLAW
           (revenue) (intel)    (skills)
```

- **Perseus** — Master orchestrator. Schedules, monitors, enforces budget.
- **Titan** — Autonomous pipeline: discover → research → email → follow up → demo site → close → build → deploy → invoice.
- **Hermes** — [Nous Research](https://github.com/NousResearch/hermes-agent) agent. Telegram, memory, self-improving skills.
- **OpenClaw** — [OpenClaw](https://github.com/openclaw/openclaw) desktop agent. 13,000+ skills via ClawHub.

## Quick Start

```bash
cp .env.example .env          # Configure API keys
pip install -r requirements.txt
make up                       # Start Docker (Postgres, Qdrant, Mem0, N8N)
make start                    # Start all daemons (Perseus, Titan, Hermes)
```

## Autonomy Rules

Perseus decides everything except:
1. Recurring cost >$50/mo or one-time >$200
2. High-risk system changes (schema, LoRA training)
3. Enterprise deals ($50K+)
4. First 10 sales (Nico approves, then full autonomy)

## Services (cheaper than market)

| Service | Price | Notes |
|---------|-------|-------|
| 5-page website | <$325 | AI-built via Bolt.new |
| Hosting | TBD | AI researches competitive pricing |
| Upsells | TBD | AI researches market rates |

## Budget: $800/month

$200 Claude Max + ~$100 Smartlead + tools/domains + GPU training buffer.

## Stack

| Component | Tool |
|-----------|------|
| AI Brain | Claude API (primary) + Ollama Qwen2.5 14B (fallback) |
| Pipeline | Titan (Python asyncio) |
| Interface | Hermes (Nous Research) + OpenClaw + Web Dashboard |
| Email | Smartlead |
| Scraping | Firecrawl + browser-use |
| Sites | Bolt.new (primary) + titan_builder (fallback) |
| DB | Postgres + Qdrant + Mem0 |
| Workflows | N8N |

## Commands

```bash
make start    # Start everything (Docker + daemons)
make stop     # Stop everything
make status   # System status
make logs     # Tail daemon logs
make health   # Quick health check
make up       # Docker services only
make down     # Stop Docker
```

## Install External Agents

```bash
./scripts/install-hermes.sh   # Install Nous Research Hermes Agent
./scripts/install-openclaw.sh # Install OpenClaw + skill collections
```

## Directory Structure

```
shared/           Config, LLM client (Claude+Ollama), DB helpers, logging
perseus/          Master daemon, scheduler, agent registry
titan/            Pipeline (10 stages), state machine, memory, review mode
hermes/           Telegram bot, alerts, web dashboard, Hermes skills
tools/            Smartlead, Firecrawl, browser, payments, budget, metrics
soul/             Personality, autonomy rules, copy guidelines
scripts/          Start/stop, install scripts, LaunchAgent plists
templates/        Industry website templates (reference)
titan_builder/    Website builder (Bolt.new fallback)
```
