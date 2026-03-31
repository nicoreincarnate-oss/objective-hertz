# Objective Hertz — Project Memory

## What This Is
Objective Hertz is a fully autonomous AI revenue system running on OpenJarvis as the orchestrator framework. Five daemons (Perseus, Titan, Hermes, ClawdBot, Conway) handle scheduling, revenue pipeline, alerts, site building, and agent economics. Runs 24/7 on Mac M4 32GB.

## Architecture
```
┌──────────────────────────────────────────────────────────────────┐
│ OPENJARVIS (Orchestrator Framework)                              │
│ ├─ WorkflowEngine DAG, A2A agent comms, security, tools         │
│ └─ orchestrator.py — top-level entry point                      │
└───────────────────────┬──────────────────────────────────────────┘
                        │ A2A + Postgres task_queue
          ┌─────────────┼───────────────┬──────────────┐
          ↓             ↓               ↓              ↓
┌─────────────┐ ┌────────────┐ ┌─────────────┐ ┌────────────────┐
│ PERSEUS     │ │ TITAN      │ │ HERMES      │ │ CLAWDBOT       │
│ Scheduler   │ │ Revenue    │ │ Alerts +    │ │ Site builder   │
│ daemon.py   │ │ daemon.py  │ │ Dashboard   │ │ + browser      │
│ scheduler.py│ │ pipeline/  │ │ daemon.py   │ │ daemon.py      │
└─────────────┘ └────────────┘ │ web/app.py  │ │ site_builder.py│
                               └─────────────┘ └────────────────┘
                                      ↕ A2A + Postgres
                               ┌─────────────┐
                               │ CONWAY      │
                               │ Economics   │
                               │ wallet.py   │
                               │ x402_client │
                               └─────────────┘
```

## The Core 4 Framework
Everything reduces to: **Context + Model + Prompt + Tools**
- **Context**: Project memory, daemon state, task queue, shared database
- **Model**: Opus (orchestration), Sonnet (execution), Haiku (simple tasks)
- **Prompt**: Specialized commands (/plan, /build, /review, /test_*) for structured work
- **Tools**: Bash, Browser automation, Database queries, Justfile, Python testing

## Directory Structure
```
orchestrator.py          → Top-level entry point (replaces legacy Perseus daemon)

openjarvis/              → Orchestrator framework
├── a2a/                 → Agent-to-agent communication layer
├── agents/              → Agent base classes and loop guard
├── core/                → WorkflowEngine, DAG runner
├── security/            → Injection scanner, SSRF, rate limiter, capabilities
├── tools/               → File, git, http, calculator, think tools
└── system.py            → System bootstrap

perseus/                 → Scheduler daemon
├── daemon.py            → Main scheduler loop
├── scheduler.py         → 15 scheduled task definitions
├── agent_registry.py    → Agent registration
└── health.py            → Health checks

titan/                   → Revenue engine
├── daemon.py            → Main pipeline daemon
├── pipeline/            → 10-stage pipeline stages
├── workflow_pipeline.py → WorkflowEngine-based pipeline
└── state_machine.py     → Task state management

hermes/                  → Alerts + dashboard
├── daemon.py            → Main event listener
├── alerts.py            → Alert dispatch
├── telegram_bot.py      → Telegram integration
├── a2a_server.py        → A2A endpoint
└── web/                 → FastAPI + War Room frontend
    ├── app.py
    └── frontend/

clawdbot/                → Site builder + browser automation
├── daemon.py            → Main daemon
├── site_builder.py      → AI site construction
├── netlify_deploy.py    → Netlify deployment
├── brain.py             → Skill routing
└── a2a_server.py        → A2A endpoint

conway/                  → Agent economics
├── wallet.py            → Base L2 USDC wallets
├── x402_client.py       → Micropayment client
├── ledger.py            → Transaction ledger
└── survival.py          → Survival-tier enforcement

shared/                  → Shared runtime layer
├── db.py                → Postgres connection pool (23 tables)
├── comms.py             → A2A + event bus
├── llm_client.py        → Claude + Ollama client
├── skill_loader.py      → Skill loading and vetting
└── magma.py             → MAGMA learning engine

tools/                   → External integrations
├── instantly_client.py  → Email campaigns
├── firecrawl_client.py  → Web scraping
├── recraft_client.py    → AI image generation
├── payment_router.py    → Stripe + Wise
└── budget_guard.py      → Spend enforcement

scripts/
├── init-db.sql          → Production schema (23 tables)
├── migrations/          → DB migration scripts
├── health-check.sh      → System health verification
└── install-launchagents.sh → macOS LaunchAgent setup

tests/                   → Pytest test suite
logs/                    → Daemon logs (perseus, titan, hermes, clawdbot)
soul/                    → Personality, autonomy rules, copywriting guidelines
templates/               → Industry website templates (dentist, plumber, restaurant)
.env                     → LIVE API KEYS (DO NOT EDIT)
docker-compose.yaml      → Service dependencies (Postgres, Qdrant, Mem0, N8N)
Makefile / justfile      → Standardized commands
CLAUDE.md                → This file
HANDOFF.md               → Context and status
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
- **Stack**: Python 3.11+, OpenJarvis, FastAPI, Postgres (psycopg v3), Mem0, Ollama, Claude API, React (War Room)
- **Deployment**: Docker Compose (local), systemd daemons (production)
- **Database**: Postgres with 23 tables (tasks, results, metrics, api_keys, events, agent wallets, leads, campaigns, and more)
- **Budget**: $800/month (Claude API calls, Postgres, Telegram)
- **Hardware**: Mac M4 32GB (development), scalable to cloud (production)
- **Monitoring**: Daemon health checks every 30 seconds, alerting via Telegram

## Success Metrics
- Task completion rate > 95%
- Pipeline latency < 60 seconds (end-to-end)
- Revenue generation accuracy > 98%
- System availability > 99% uptime
- Alert delivery < 5 second latency

## Security & Compliance Rules (AEGIS Audit — 2026-03-30)

These rules derive from the AEGIS diagnostic audit (91 findings, 11 agents). Full report: `.aegis/report/AEGIS-REPORT.md`.

### SQL Safety
- **Never** construct SQL with f-strings, `.format()`, or concatenation — use parameterized queries (`%s`) for values and `sql.Identifier()` for dynamic table/column names
- Validate all dynamic identifiers against an explicit allowlist before use
- Enforcement: `grep -rn "f\".*SELECT\|f\".*INSERT\|f\".*UPDATE\|f\".*DELETE" --include="*.py" shared/ titan/ hermes/ clawdbot/ conway/ openjarvis/` must return zero hits

### Credential & Secret Safety
- Credential stripper (`openjarvis/security/credential_stripper.py`) must cover ALL service token patterns: Stripe (`sk_live_`, `sk_test_`, `pk_live_`, `rk_live_`), Telegram bot tokens, Netlify tokens, Instantly API keys, database connection strings, JWT, GitHub tokens — minimum 15 patterns
- When adding a new external service integration, add its token pattern to the credential stripper in the same PR
- All `subprocess.run()` calls must use `shell=False` with list args, or be wrapped by the subprocess sandbox. Never remove `requires_confirmation=True` from ShellExecTool
- No `eval()` or `exec()` in production code — use `simpleeval` library for expression evaluation
- All `torch.load()` calls must use `weights_only=True`
- Scraped web content must be truncated to 50KB max before LLM input

### Financial Safety
- Budget guard (`shared/llm_client.py`) must **fail closed** — DB errors reject the API call and fall back to Ollama, never allow the call through
- Review mode transition (True→False) must never happen automatically — require Telegram operator confirmation
- `/api/config` must not allow direct `review_mode` changes — use dedicated endpoint with confirmation token
- Every function in `conway/wallet.py` and `tools/payment_router.py` must have integration tests

### Email Compliance (CAN-SPAM)
- Titan daemon must **refuse to send emails** if `physical_address` system config matches `[SET YOUR` or is empty — check at startup AND before each batch
- Unsubscribe check must use `SELECT ... FOR UPDATE` inside the same transaction as the send call (prevent TOCTOU race)
- Email simulation results must be stored in DB; send query must include `WHERE simulation_status = 'passed'`

### Integration & Wiring
- `shared/middleware.py` must be imported and called by `titan/daemon.py` — the middleware chain must execute in production, not just exist as dead code
- `ENABLE_MIDDLEWARE` must default to `true` — disabling logs a WARNING at startup
- CI pipeline must include security module tests (`openjarvis/security/`) — don't exclude them
- A2A endpoints must validate a shared-secret token before dispatching any capability

### Architecture
- No `allow_origins=["*"]` in CORS configuration — use explicit allowed origins
- Session cookies must default to `Secure=True` (development is the explicit exception, not the other way around)
- `close_pool()` in `shared/db.py` must acquire `_pool_lock` before setting `_pool = None`
- Docker containers must run as non-root (`USER` directive required in all Dockerfiles)
- Config changes via `set_config()` must log to `config_audit_log` table with `changed_by` parameter

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
