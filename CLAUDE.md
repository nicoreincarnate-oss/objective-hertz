# Perseus — Project Memory

## What This Is
Perseus is a fully autonomous AI revenue system that orchestrates a 4-daemon autonomous pipeline to generate consistent, verifiable revenue. It combines intelligent task scheduling, multi-stage revenue generation, event dispatch, and browser automation into a cohesive agentic engineering platform running 24/7.

## Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│ PERSEUS (Master Scheduler)                                      │
│ ├─ Inserts tasks into task_queue every 10 seconds              │
│ └─ Reads config, manages pipeline lifecycle                    │
└────────────────────┬────────────────────────────────────────────┘
                     │ task_queue (shared state)
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│ TITAN (Revenue Engine)                                          │
│ ├─ Polls task_queue continuously                               │
│ ├─ Executes 10-stage revenue generation pipeline               │
│ └─ Writes results to postgres + triggers events                │
└────────────────┬──────────────────────┬───────────────────────────┘
                 │ events               │ postgres (shared state)
                 ↓                      ↓
┌────────────────────────────────┐  ┌──────────────────────────────┐
│ HERMES (Alerts + Web)          │  │ Persistent Data Layer        │
│ ├─ Listens for TITAN events    │  │ ├─ Tasks, results, metrics   │
│ ├─ Dispatches Telegram alerts  │  │ └─ API key management        │
│ └─ Serves Next.js dashboard    │  │                              │
└────────────────────────────────┘  └──────────────────────────────┘
         ↑                                    ↑
         └────────────┬──────────────────────┘
                      │
         ┌────────────↓──────────────────┐
         │ CLAWDBOT (Skills + Browser)   │
         │ ├─ Executes remote skills     │
         │ ├─ Runs browser automation    │
         │ ├─ Scrapes and verifies       │
         │ └─ Reports back to HERMES     │
         └───────────────────────────────┘
```

## The Core 4 Framework
Everything reduces to: **Context + Model + Prompt + Tools**
- **Context**: Project memory, daemon state, task queue, shared database
- **Model**: Opus (orchestration), Sonnet (execution), Haiku (simple tasks)
- **Prompt**: Specialized commands (/plan, /build, /review, /test_*) for structured work
- **Tools**: Bash, Browser automation, Database queries, Justfile, Python testing

## Directory Structure
```
perseus/
├── main.py              → Master scheduler daemon
├── config.yaml          → Task generation config
├── timing.py            → Schedule management
└── monitoring.py        → Health checks

titan/
├── main.py              → Revenue engine core
├── pipeline.py          → 10-stage revenue pipeline
├── stages/              → Individual pipeline stages
└── processors.py        → Data processing logic

hermes/
├── main.py              → Event dispatcher daemon
├── telegram.py          → Telegram integration
├── api.py               → FastAPI server
└── dashboard/           → Next.js web interface

clawdbot/
├── main.py              → Skill + browser automation daemon
├── skills/              → Reusable skill library
├── browser/             → Browser automation layer
└── verification.py      → Result verification

shared/
├── models.py            → Pydantic schemas
├── queue.py             → Task queue implementation
├── db.py                → Postgres connection pool
└── events.py            → Event schema and dispatch

tools/
├── cli.py               → CLI utilities
├── deployment.py        → Deployment helpers
├── monitoring.py        → Metrics and logging
└── backup.py            → Data backup utilities

tests/
├── test_perseus.py      → Scheduler tests
├── test_titan.py        → Pipeline tests
├── test_hermes.py       → API and alerts tests
├── test_clawdbot.py     → Browser automation tests
└── conftest.py          → Pytest fixtures

.claude/
├── settings.local.json  → Hook wiring, local config
├── hooks/               → Validation and damage control
│   ├── validators/      → Post-tool validation scripts
│   └── patterns.yaml    → Protected paths and blocked commands
└── commands/            → Specialized prompts
    ├── plan.md          → Planning template
    ├── build.md         → Build execution
    ├── review.md        → Code review
    └── test_*.md        → Testing commands

scripts/
├── init-db.sql          → Production schema
├── migrate.py           → Database migrations
├── health-check.sh      → System health verification
└── local-setup.sh       → Development environment setup

logs/
├── perseus.log          → Scheduler logs
├── titan.log            → Revenue engine logs
├── hermes.log           → Event dispatcher logs
└── clawdbot.log         → Automation logs

.env                    → LIVE API KEYS (DO NOT EDIT)
.env.local              → Development overrides
docker-compose.yml      → Service dependencies
Justfile                → Standardized commands
README.md               → Project overview
HANDOFF.md              → Context and status
CLAUDE.md               → This file
```

## Agent Instructions

### Do
- Use `/prime` before starting any major work
- Use specialized commands (/plan, /build, /review, /test_perseus) instead of generic prompting
- Check `/context` regularly for token consumption
- Use skills over MCP servers when possible (context efficiency)
- Run validation hooks — they exist for a reason
- Reset context between phases (new agent per phase)
- Use justfile commands for standardized operations
- Always run `ruff check` and `pytest` after changes to Python files
- Check damage control patterns before running destructive commands
- Use `make status` to check daemon health before starting work
- Verify database connection with `make health` before running migrations
- Check logs/ directory for daemon errors and failure patterns
- Document all changes in HANDOFF.md for context continuity

### Don't
- Don't use `rm -rf` or force-delete anything
- Don't modify `.claude/settings.local.json` or `.claude/hooks/` without explicit permission
- Don't skip validation steps
- Don't accumulate context across phases — reset instead
- Don't use MCP servers when a CLI-teaching prompt will do
- Don't touch `.env` files directly (contains live API keys and secrets)
- Don't run `DROP TABLE`, `TRUNCATE`, or `DELETE FROM` without confirmation
- Don't modify running daemons without stopping them first (`make stop`)
- Don't push to main without running `make quality` first
- Don't restart all daemons during testing (start/stop individual ones)
- Don't commit changes to logs/ or temporary files
- Don't hardcode credentials or API keys in code

## Common Issues & Resolutions
- **Problem**: Agent context window filling up
  **Solution**: Start new agent with fresh context. Use `/prime` to reload essentials.
- **Problem**: Build failing after changes
  **Solution**: Run `/review` to identify issues, then targeted `/build` to fix.
- **Problem**: Daemon not starting
  **Solution**: Check `make status`, review logs/ directory, verify Docker services with `make health`
- **Problem**: Database connection refused
  **Solution**: Run `make up` to start Docker services, wait 10 seconds, retry
- **Problem**: Tests failing on import
  **Solution**: Run with `PYTHONPATH=. python3 -m pytest tests/ -v`
- **Problem**: Validation hook blocking legitimate command
  **Solution**: Check `.claude/hooks/patterns.yaml`, add exception if truly needed. Never disable the hook.
- **Problem**: Task queue backing up
  **Solution**: Check TITAN logs for stuck stages, verify database indices with `make health`
- **Problem**: Telegram alerts not sending
  **Solution**: Verify HERMES daemon is running (`make status`), check `.env` for token, review hermes.log
- **Problem**: Browser automation timing out
  **Solution**: Check CLAWDBOT logs, increase timeouts in clawdbot/browser/config.yaml, verify network

## Model Strategy
- **Opus**: Orchestrator, planning, complex delegation, pipeline architecture, debugging multi-daemon issues
- **Sonnet**: Standard execution, building, reviewing, daemon modifications, Python development
- **Haiku**: Simple tasks, summarization, 3x productivity for same cost, log analysis, quick fixes

## Key Commands
```
/prime              → Load project context and daemon state
/plan               → Create structured plan with timeline
/build              → Execute from plan with validation
/review             → Review changes and identify issues
/test_perseus       → Run full test suite (ruff + mypy + pytest)
/test_titan         → Test Titan pipeline stages in isolation
/test_hermes        → Test Hermes API and alert dispatch
/test_clawdbot      → Test browser automation and skills
/health_perseus     → Full system health (daemons + docker + DB)
/reproduce_bug      → Reproduce and document bug with logs
/damage-control     → List active protections and hooks
/context_audit      → Token usage analysis and optimization
/make status        → Check all daemon health
/make health        → Verify Docker and database
/make logs          → Tail all daemon logs
/make quality       → Run ruff, mypy, pytest before commits
```

## Protected Paths
- `.env`, `.env.*` — ZERO ACCESS (live API keys, Stripe secrets, Claude API credentials)
- `.claude/settings.local.json` — READ ONLY (hook wiring, local configuration)
- `.claude/hooks/` — READ ONLY (validation scripts and patterns)
- `scripts/init-db.sql` — READ ONLY (production schema)
- `CLAUDE.md`, `HANDOFF.md`, `README.md` — NO DELETE (project memory and documentation)
- Database tables (require confirmation before DROP/TRUNCATE/DELETE)
- Running daemon processes (require `make stop` before modification)

## Infrastructure
- **Stack**: Python 3.11+, FastAPI, Postgres (psycopg v3), Mem0, Ollama, Claude API, Next.js
- **Deployment**: Docker Compose (local), systemd daemons (production)
- **Database**: Postgres with 5 core tables (tasks, results, metrics, api_keys, events)
- **Budget**: $800/month (Claude API calls, Postgres, Telegram)
- **Hardware**: Mac M4 32GB (development), scalable to cloud (production)
- **Monitoring**: Daemon health checks every 30 seconds, alerting via Telegram

## Success Metrics
- Task completion rate > 95%
- Pipeline latency < 60 seconds (end-to-end)
- Revenue generation accuracy > 98%
- System availability > 99% uptime
- Alert delivery < 5 second latency
