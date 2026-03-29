# Codebase Structure

**Analysis Date:** 2026-03-27

## Directory Layout

```
objective-hertz/
├── orchestrator.py              # Top-level entry point (THE boss)
├── CLAUDE.md                    # Project memory and instructions
├── HANDOFF.md                   # Context continuity between sessions
├── Makefile                     # Standardized commands
├── justfile                     # Alternative task runner
├── docker-compose.yaml          # Postgres, Qdrant, Mem0, N8N
├── .env                         # LIVE API keys (DO NOT READ)
│
├── openjarvis/                  # Orchestrator framework (reusable)
│   ├── core/                    #   Registries, types, EventBus
│   ├── a2a/                     #   Agent-to-Agent protocol (Google A2A)
│   ├── security/                #   Scanners, RBAC, SSRF, audit
│   ├── vassals/                 #   Supervisor, discovery, scheduler, relay
│   ├── tools/                   #   OJ-native tools (file, git, http, think)
│   ├── agents/                  #   Agent base classes, templates, loop guard
│   ├── learning/                #   Optimization, routing, training, feedback
│   ├── engine/                  #   Inference engine abstraction
│   ├── scheduler/               #   Task scheduling primitives
│   ├── server/                  #   OJ HTTP server
│   ├── traces/                  #   Trace storage
│   ├── bench/                   #   Benchmarking
│   ├── evals/                   #   Evaluation framework
│   └── optimize/                #   Configuration optimization
│
├── titan/                       # Revenue engine daemon
│   ├── daemon.py                #   Main loop + A2A server
│   ├── state_machine.py         #   Lead status transitions
│   ├── memory.py                #   Learning and reflection
│   ├── deliverability.py        #   Email deliverability monitoring
│   ├── expansion.py             #   Revenue expansion proposals
│   ├── training.py              #   LoRA fine-tuning pipeline
│   └── pipeline/                #   10-stage revenue pipeline
│       ├── lead_discovery.py    #     Stage 1: Find prospects
│       ├── lead_research.py     #     Stage 2: Deep research
│       ├── email_compose.py     #     Stage 3: Draft emails
│       ├── email_send.py        #     Stage 4: Send via Instantly
│       ├── follow_up.py         #     Stage 5: Follow-up management
│       ├── close_deal.py        #     Stage 6-7: Demo + close
│       ├── build_site.py        #     Stage 8: Full site build
│       ├── deploy_site.py       #     Stage 9: Netlify deploy
│       └── invoice.py           #     Stage 10: Payment collection
│
├── hermes/                      # Alerts + dashboard daemon
│   ├── daemon.py                #   Main loop (Telegram + alerts + web)
│   ├── alerts.py                #   Alert dispatch logic
│   ├── telegram_bot.py          #   Telegram bot interface
│   ├── a2a_server.py            #   A2A endpoint
│   ├── skills/                  #   Hermes-specific skills
│   └── web/                     #   Web dashboard
│       ├── app.py               #     FastAPI backend (port 8500)
│       ├── presenter.py         #     View model builder
│       ├── operator_chat.py     #     Operator command dispatch
│       ├── routes/              #     API route modules
│       ├── templates/           #     Jinja2 HTML templates
│       ├── static/              #     Static assets
│       └── frontend/            #     Next.js War Room
│           ├── app/             #       Next.js app router pages
│           │   ├── page.tsx     #         Home/dashboard
│           │   ├── pipeline/    #         Pipeline view
│           │   ├── agents/      #         Agent monitoring
│           │   ├── intel/       #         Intelligence view
│           │   ├── settings/    #         Settings page
│           │   └── api/         #         API routes (Next.js)
│           ├── components/      #       React components
│           │   ├── ui/          #         shadcn/ui primitives
│           │   └── blocks/      #         Composite components
│           ├── contexts/        #       React context providers
│           ├── hooks/           #       Custom React hooks
│           └── lib/             #       Utility functions
│
├── clawdbot/                    # Site builder + browser automation
│   ├── daemon.py                #   Main loop + skill execution
│   ├── site_builder.py          #   AI site construction
│   ├── netlify_deploy.py        #   Netlify deployment
│   ├── brain.py                 #   Skill routing
│   └── a2a_server.py            #   A2A endpoint
│
├── conway/                      # Agent economics
│   ├── wallet.py                #   Base L2 USDC wallets
│   ├── x402_client.py           #   Micropayment client
│   ├── ledger.py                #   Transaction ledger
│   └── survival.py              #   Survival-tier enforcement
│
├── shared/                      # Shared runtime (used by ALL agents)
│   ├── config.py                #   Central config (loads .env)
│   ├── db.py                    #   Postgres async pool (psycopg v3)
│   ├── comms.py                 #   Inter-agent comms (A2A + DB fallback)
│   ├── llm_client.py            #   Claude + Ollama unified client
│   ├── agent_base.py            #   Base class for daemon agents
│   ├── oj_bridge.py             #   OpenJarvis singleton bridge
│   ├── task_routing.py          #   Task type -> agent name map
│   ├── capability_router.py     #   Dynamic capability routing
│   ├── skill_loader.py          #   Multi-directory skill loader
│   ├── observability.py         #   Traces, Sentry, Prometheus
│   ├── logging_config.py        #   Structured logging setup
│   ├── magma.py                 #   MAGMA graph memory
│   ├── pipeline.py              #   Pipeline state assessment
│   ├── pipeline_alerts.py       #   Pipeline error events
│   ├── pipeline_dag.py          #   DAG-based pipeline execution
│   └── (20+ more modules)       #   Bandit, RAG, inference opt, etc.
│
├── tools/                       # External service integrations
│   ├── instantly_client.py      #   Email campaigns (Instantly.ai)
│   ├── firecrawl_client.py      #   Web scraping (Firecrawl)
│   ├── recraft_client.py        #   AI image generation (Recraft)
│   ├── payment_router.py        #   Stripe + Wise payments
│   ├── budget_guard.py          #   Budget enforcement
│   ├── domain_manager.py        #   Domain provisioning
│   ├── n8n_client.py            #   N8N workflow automation
│   ├── notebooklm_client.py     #   NotebookLM integration
│   └── runtime_honesty.py       #   Runtime honesty checks
│
├── scripts/                     # Ops scripts
│   ├── init-db.sql              #   Production schema (23+ tables)
│   ├── migrations/              #   DB migration scripts
│   ├── health-check.sh          #   System health verification
│   └── install-launchagents.sh  #   macOS LaunchAgent setup
│
├── tests/                       # Pytest test suite
├── templates/                   # Website templates (dentist, plumber, etc.)
├── soul/                        # Personality, autonomy rules, copywriting
├── logs/                        # Daemon logs (not committed)
├── data/                        # Runtime data directory
├── design-system/               # Design mockups for War Room
└── intel/                       # Intelligence/research data
```

## Directory Purposes

**`openjarvis/`:**
- Purpose: The reusable orchestrator framework. Could theoretically be extracted as a library.
- Contains: Core infrastructure (EventBus, registries, types), A2A protocol, security layer, vassal management, learning/optimization subsystem, evaluation framework
- Key files: `core/events.py` (EventBus), `a2a/server.py` + `a2a/client.py` (protocol), `security/__init__.py` (security setup), `vassals/supervisor.py` (process management)

**`titan/`:**
- Purpose: The revenue engine. Discovers leads, nurtures them through email, closes deals, builds sites, collects payment.
- Contains: 10 pipeline stages, state machine, learning/reflection, deliverability monitoring
- Key files: `daemon.py` (entry point), `pipeline/lead_discovery.py` through `pipeline/invoice.py` (the full pipeline), `state_machine.py` (transition enforcement)

**`hermes/`:**
- Purpose: Communications and monitoring. Telegram bot, alert dispatch, web dashboard.
- Contains: Daemon, alert logic, Telegram integration, FastAPI backend, Next.js frontend
- Key files: `daemon.py` (entry point), `web/app.py` (FastAPI), `web/frontend/app/` (Next.js pages)

**`clawdbot/`:**
- Purpose: Execution agent. Builds websites, runs browser automation, executes skills.
- Contains: Daemon, site builder, Netlify deployer, skill brain
- Key files: `daemon.py` (entry point), `site_builder.py` (AI site construction), `brain.py` (skill routing)

**`conway/`:**
- Purpose: Agent economics. Manages Ethereum wallets on Base L2 for agent-to-agent micropayments.
- Contains: Wallet management, x402 payment protocol, transaction ledger, survival tiers
- Key files: `wallet.py` (Base L2 USDC wallets), `x402_client.py` (micropayments)

**`shared/`:**
- Purpose: Runtime glue used by every daemon. Database, comms, LLM, config, observability.
- Contains: 35+ modules covering DB, comms, LLM, config, skills, memory, observability, and experimental features
- Key files: `db.py` (Postgres pool), `comms.py` (A2A + DB comms), `llm_client.py` (Claude + Ollama), `config.py` (central config), `oj_bridge.py` (OJ singletons)

**`tools/`:**
- Purpose: External service client libraries
- Contains: API clients for Instantly, Firecrawl, Recraft, Stripe, Wise, N8N, etc.
- Key files: `budget_guard.py` (spend enforcement -- critical for autonomy), `payment_router.py` (revenue collection)

## Key File Locations

**Entry Points:**
- `orchestrator.py`: THE entry point. Starts everything.
- `titan/daemon.py`: Titan revenue engine (spawned by orchestrator)
- `hermes/daemon.py`: Hermes alerts + dashboard (spawned by orchestrator)
- `clawdbot/daemon.py`: ClawdBot site builder (spawned by orchestrator)

**Configuration:**
- `shared/config.py`: Central typed config loaded from .env
- `scripts/init-db.sql`: Database schema definition
- `docker-compose.yaml`: Service dependencies (Postgres, Qdrant, Mem0, N8N)
- `openjarvis/vassals/supervisor.py`: Vassal process configs (commands, ports, restart policy)

**Core Logic:**
- `titan/pipeline/`: The 10-stage revenue pipeline (the money-making code)
- `titan/state_machine.py`: Lead status transitions (enforces pipeline integrity)
- `openjarvis/vassals/perseus_scheduler.py`: Strategic brain (tick loop, priorities, budget)
- `shared/comms.py`: Inter-agent communication (A2A primary, DB fallback)
- `shared/llm_client.py`: Budget-aware LLM routing

**Testing:**
- `tests/`: Pytest test suite (579+ tests)

**Security:**
- `openjarvis/security/`: Full security stack (scanners, RBAC, SSRF, audit)
- `hermes/web/app.py`: Dashboard authentication (HMAC session cookies)

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `lead_discovery.py`, `budget_guard.py`)
- Daemon entry points: `daemon.py` in each agent directory
- Config/schema: Descriptive names (`init-db.sql`, `docker-compose.yaml`)

**Directories:**
- Agent directories: lowercase single word (`titan/`, `hermes/`, `clawdbot/`, `conway/`)
- Framework directories: lowercase with underscores (`openjarvis/`, `shared/`)
- Pipeline stages: `titan/pipeline/` with descriptive module names

**Classes:**
- PascalCase: `AgentBase`, `VassalSupervisor`, `PerseusScheduler`, `LLMClient`
- Daemon classes: `{Name}Daemon` (e.g., `HermesDaemon`)
- Config dataclasses: `{Name}Config` (e.g., `PostgresConfig`, `PerseusConfig`)

## Where to Add New Code

**New Pipeline Stage:**
- Primary code: `titan/pipeline/new_stage.py`
- Add transition in: `titan/state_machine.py` TRANSITIONS dict
- Wire into daemon loop: `titan/daemon.py` (import and add to scheduled stages)
- DB schema changes: `scripts/migrations/` (new migration file)
- Tests: `tests/test_new_stage.py`

**New External Integration:**
- Client library: `tools/new_client.py`
- Config: Add dataclass in `shared/config.py`, add env vars
- Task routing: Add task types to `shared/task_routing.py`
- Tests: `tests/test_new_client.py`

**New Daemon/Agent:**
- Daemon: `new_agent/daemon.py` (subclass `shared/agent_base.py`)
- A2A server: `new_agent/a2a_server.py`
- Register in supervisor: `openjarvis/vassals/supervisor.py` `register_defaults()`
- Add A2A URL: `shared/oj_bridge.py` AGENT_URLS dict
- Add port: Set env var `NEW_AGENT_A2A_PORT`
- Task routing: `shared/task_routing.py`

**New Dashboard Page:**
- Next.js page: `hermes/web/frontend/app/new-page/page.tsx`
- API route: `hermes/web/frontend/app/api/new-endpoint/route.ts`
- Components: `hermes/web/frontend/components/blocks/NewComponent.tsx`
- Backend API: `hermes/web/routes/new_route.py` or add to `hermes/web/app.py`

**New Skill:**
- Skill definition: `hermes/skills/new-skill/SKILL.md`
- Alternative locations: `~/.openclaw/skills/new-skill/SKILL.md`
- No code changes needed -- skill loader auto-discovers from SKILL_DIRS

**New OpenJarvis Feature:**
- Core primitive: `openjarvis/core/new_module.py`
- Security feature: `openjarvis/security/new_scanner.py`
- Learning feature: `openjarvis/learning/new_module.py`
- Register in `__init__.py` of the relevant subpackage

## Special Directories

**`logs/`:**
- Purpose: Daemon log files (perseus.log, titan.log, hermes.log, clawdbot.log)
- Generated: Yes, at runtime
- Committed: No (in .gitignore)

**`data/`:**
- Purpose: Runtime data (OJ trace store, cached data)
- Generated: Yes, at runtime
- Committed: No

**`MagicMock/`:**
- Purpose: Artifact from test mocking (should be cleaned up)
- Generated: Yes, accidentally
- Committed: Should not be

**`soul/`:**
- Purpose: Agent personality definitions, autonomy rules, copywriting guidelines
- Generated: No (hand-authored)
- Committed: Yes

**`templates/`:**
- Purpose: Industry-specific website templates (dentist, plumber, restaurant, etc.)
- Generated: No (designed templates)
- Committed: Yes

**`design-system/`:**
- Purpose: Visual design mockups for the War Room dashboard
- Generated: No
- Committed: Yes

---

*Structure analysis: 2026-03-27*
