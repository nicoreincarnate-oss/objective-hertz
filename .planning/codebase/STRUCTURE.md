# Project Structure

**Analysis Date:** 2026-03-29

## Directory Tree

```
objective-hertz/
├── orchestrator.py              # Main entry point (OpenJarvis boss)
├── CLAUDE.md                    # Project memory and instructions
├── HANDOFF.md                   # Context continuity document
├── Makefile / justfile           # Standardized commands
├── docker-compose.yaml           # Postgres, Qdrant, Mem0, N8N
├── .env                          # LIVE API KEYS (DO NOT READ)
│
├── openjarvis/                   # Orchestrator framework (447 py files)
│   ├── a2a/                      # Agent-to-agent protocol (client, server, protocol, tool)
│   ├── agents/                   # Agent base classes and loop guard
│   ├── channels/                 # Communication channels
│   ├── core/                     # EventBus, config, credentials, types, registry
│   ├── daemon/                   # Daemon lifecycle management
│   ├── engine/                   # Execution engine
│   ├── intelligence/             # Intelligence gathering
│   ├── learning/                 # Agent learning infrastructure
│   ├── mcp/                      # Model Context Protocol integration
│   ├── operators/                # Operator pattern (system_monitor, etc.)
│   ├── prompt/                   # Prompt management
│   ├── recipes/                  # Pre-built workflow recipes
│   ├── sandbox/                  # Sandboxed execution
│   ├── scheduler/                # Agent scheduling
│   ├── security/                 # Injection scanner, SSRF, rate limiter, capabilities, audit
│   ├── sessions/                 # Session management
│   ├── skills/                   # Skill infrastructure
│   ├── tools/                    # 30+ built-in tools (browser, file, git, http, shell, etc.)
│   ├── vassals/                  # Vassal management (14 files)
│   │   ├── supervisor.py         # Spawn/monitor daemon processes
│   │   ├── discovery.py          # Discover A2A capabilities
│   │   ├── perseus_scheduler.py  # Strategic brain (replaces Perseus daemon)
│   │   ├── event_relay.py        # Bidirectional event bridge
│   │   ├── sleep_cycle.py        # Nightly Alpha/Beta debate + backprop
│   │   ├── backprop.py           # Behavioral file edit engine
│   │   ├── cell_division.py      # Propose new specialized agents
│   │   ├── memory_federation.py  # Cross-daemon memory
│   │   ├── infra_health.py       # Infrastructure health checks
│   │   ├── priorities.py         # Deterministic priority logic
│   │   ├── schedules.py          # Schedule definitions
│   │   └── registry.py           # Agent registry + heartbeat
│   ├── workflow/                  # WorkflowGraph DAG execution
│   └── system.py                 # SystemBuilder bootstrap
│
├── perseus/                      # Legacy scheduler daemon (11 py files)
│   ├── daemon.py                 # LEGACY — refuses to start if orchestrator running
│   ├── scheduler.py              # 15 scheduled task definitions
│   ├── agent_registry.py         # Agent registration
│   └── health.py                 # Health checks
│
├── titan/                        # Revenue engine (25 py files)
│   ├── daemon.py                 # Main daemon loop (TitanDaemon)
│   ├── pipeline/                 # 10-stage pipeline
│   │   ├── lead_discovery.py     # Stage 1: Find businesses
│   │   ├── lead_research.py      # Stage 2: Research leads
│   │   ├── email_compose.py      # Stage 3: Write custom emails
│   │   ├── email_send.py         # Stage 4: Send + Stage 5b: Sync analytics
│   │   ├── follow_up.py          # Stage 5a: Process follow-ups
│   │   ├── close_deal.py         # Stage 6: Close interested leads
│   │   ├── build_site.py         # Stage 7: Build websites
│   │   ├── deploy_site.py        # Stage 8: Deploy sites
│   │   └── invoice.py            # Stage 9: Process invoices
│   ├── expansion.py              # Revenue-gated self-expansion engine
│   ├── workflow_pipeline.py      # DAG definition (WorkflowGraph)
│   ├── state_machine.py          # Lead state transitions
│   ├── memory.py                 # Titan's memory layer (Mem0, learnings, reflection)
│   ├── compliance.py             # CAN-SPAM compliance (FATAL on failure)
│   ├── deliverability.py         # Email deliverability monitoring
│   ├── training.py               # LoRA training pipeline
│   ├── a2a_server.py             # Titan A2A endpoint
│   └── workflow_tools.py         # Pipeline tools for WorkflowEngine
│
├── hermes/                       # Alerts + dashboard (10 py files)
│   ├── daemon.py                 # Main daemon (HermesDaemon)
│   ├── alerts.py                 # Alert dispatch logic
│   ├── telegram_bot.py           # Telegram bot integration
│   ├── a2a_server.py             # Hermes A2A endpoint
│   └── web/                      # FastAPI War Room dashboard
│       ├── app.py                # FastAPI app (routes, API endpoints)
│       └── frontend/             # React frontend
│
├── clawdbot/                     # Site builder + browser automation (11 py files)
│   ├── daemon.py                 # Main daemon (ClawdBotDaemon)
│   ├── site_builder.py           # AI site construction
│   ├── netlify_deploy.py         # Netlify deployment
│   ├── brain.py                  # Skill routing
│   └── a2a_server.py             # ClawdBot A2A endpoint
│
├── conway/                       # Agent economics (10 py files)
│   ├── wallet.py                 # Base L2 USDC wallets (AgentWallet, WalletManager)
│   ├── x402_client.py            # x402 micropayment protocol client
│   ├── ledger.py                 # Transaction ledger (conway_ledger table)
│   ├── survival.py               # Survival tier management
│   ├── identity.py               # Agent identity
│   ├── cloud.py                  # Cloud wallet integration
│   ├── runtime.py                # Runtime economic controls
│   ├── registry.py               # Economic registry
│   └── terminal.py               # Terminal interface
│
├── shared/                       # Shared runtime layer (26 py files)
│   ├── db.py                     # Postgres connection pool (23 tables)
│   ├── comms.py                  # Inter-daemon communication API
│   ├── llm_client.py             # Claude + Ollama client (fast/smart/genius tiers)
│   ├── config.py                 # Configuration management
│   ├── agent_base.py             # AgentBase ABC (all daemons extend this)
│   ├── skill_loader.py           # Skill loading, vetting, execution
│   ├── magma.py                  # MAGMA graph memory (Neo4j + Qdrant + Zep)
│   ├── oj_bridge.py              # OpenJarvis singleton bootstrap
│   ├── pipeline.py               # Pipeline state assessment
│   ├── pipeline_alerts.py        # Pipeline error emission
│   ├── task_routing.py           # Task type -> agent name routing
│   ├── capability_router.py      # Dynamic capability routing
│   ├── self_model.py             # Agent self-assessment metrics
│   ├── observability.py          # Tracing, metrics, exception capture
│   ├── logging_config.py         # Structured logging setup
│   ├── test_time_learning.py     # Test-time learning hooks
│   ├── weight_directives.py      # Weight/priority directives
│   ├── inference_optimizer.py    # Inference cost optimization
│   ├── execution_loop.py         # Execution loop utilities
│   ├── adaptive_dashboard.py     # Adaptive dashboard data
│   ├── bandit.py                 # Multi-armed bandit
│   └── prospect_simulator.py     # Prospect simulation
│
├── tools/                        # External integrations (532 py files - includes vendored SDKs)
│   ├── instantly_client.py       # Email campaign API (Instantly.ai)
│   ├── firecrawl_client.py       # Web scraping API
│   ├── recraft_client.py         # AI image generation
│   ├── payment_router.py         # Stripe + Wise payment routing
│   ├── budget_guard.py           # Spend enforcement
│   ├── domain_manager.py         # Custom domain management
│   ├── n8n_client.py             # N8N workflow automation
│   ├── notebooklm_client.py      # NotebookLM integration
│   ├── runtime_honesty.py        # Runtime honesty checks
│   ├── browser-use/              # Browser automation library (vendored)
│   └── firecrawl/                # Firecrawl SDK (vendored)
│
├── soul/                         # Personality and guidelines
│   ├── soul_agent.md             # Agent behavioral rules
│   ├── soul_copy.md              # Copywriting guidelines + compliance rules
│   ├── soul_hermes.md            # Hermes-specific personality
│   ├── soul_values.md            # Core values
│   └── templates/                # Outreach templates
│       ├── electrician_outreach.md
│       ├── home_services_outreach.md
│       └── plumber_outreach.md
│
├── .agent/skills/                # Installed skill packages
│   ├── firecrawl-skill/          # Firecrawl search skill
│   └── marketing/                # Marketing skill collection (40+ skills)
│       └── skills/               # Individual skills (cold-email, ai-seo, copywriting, etc.)
│
├── scripts/                      # Operational scripts
│   ├── init-db.sql               # Production schema (23 tables)
│   ├── migrations/               # DB migration scripts
│   ├── health-check.sh           # System health verification
│   └── install-launchagents.sh   # macOS LaunchAgent setup
│
├── tests/                        # Pytest test suite (457 py files)
│   ├── test_week*.py             # Weekly test suites
│   ├── openjarvis/               # OpenJarvis framework tests
│   └── helpers/                  # Test helpers
│
├── templates/                    # Industry website templates
├── logs/                         # Daemon logs (not committed)
├── intel/                        # Research intelligence documents
│   ├── agent-dna/                # Agent DNA research
│   ├── anti-slop/                # Anti-slop research
│   ├── deerflow/                 # DeerFlow memory middleware research
│   ├── hyperagents/              # HyperAgents research
│   ├── post-quantum-crypto/      # PQC research
│   ├── quantum-computing/        # Quantum computing research
│   └── recursive-language-models/ # RLM research
└── .planning/                    # GSD planning documents
```

## Module Dependency Graph

### Cross-Daemon Dependencies (what imports what)

```
orchestrator.py
  -> shared/config.py, shared/logging_config.py, shared/observability.py
  -> shared/oj_bridge.py (EventBus, AgentManager, TraceStore, AuditLogger)
  -> shared/db.py (init_pool, fetch_all, execute)
  -> openjarvis/vassals/* (supervisor, discovery, perseus_scheduler, event_relay)
  -> openjarvis/system.py (SystemBuilder)
  -> openjarvis/operators/manager.py (OperatorManager)
  -> tools/budget_guard.py

titan/daemon.py
  -> shared/agent_base.py (AgentBase)
  -> shared/db.py, shared/logging_config.py, shared/observability.py
  -> openjarvis/vassals/registry.py (heartbeat)
  -> titan/pipeline/*.py (all stage handlers)
  -> titan/expansion.py, titan/memory.py, titan/training.py, titan/deliverability.py
  -> titan/compliance.py (assert_compliance_ready)
  -> shared/comms.py (ask_agent, delegate_task, get_pending_recommendations)

hermes/daemon.py
  -> shared/agent_base.py, shared/db.py, shared/config.py
  -> hermes/alerts.py, hermes/telegram_bot.py
  -> hermes/web/app.py (FastAPI dashboard)
  -> shared/comms.py (ask_agent, send_alert)
  -> shared/llm_client.py (LLM routing for operator messages)

clawdbot/daemon.py
  -> shared/agent_base.py, shared/db.py, shared/config.py
  -> shared/skill_loader.py (execute_skill, find_skill, list_installed_skills)
  -> shared/comms.py (record_decision)
  -> openjarvis/vassals/registry.py (heartbeat)

shared/comms.py (central hub)
  -> shared/db.py (all DB operations)
  -> shared/oj_bridge.py (call_agent_async for A2A)
  -> shared/task_routing.py (TASK_ROUTING)
  -> shared/capability_router.py (dynamic routing)
  -> shared/observability.py (enrich_payload_with_context)
```

### Key Shared Dependencies

All daemons depend on:
- `shared/agent_base.py` — AgentBase ABC
- `shared/db.py` — Postgres connection pool
- `shared/comms.py` — Inter-daemon communication
- `shared/config.py` — Configuration
- `shared/logging_config.py` — Structured logging
- `shared/observability.py` — Tracing and metrics

## Entry Points

### Daemon Startup
| Daemon | Command | Entry Function |
|--------|---------|----------------|
| Orchestrator | `python orchestrator.py` | `Orchestrator.start()` |
| Titan | `python -m titan.daemon` | `TitanDaemon.start()` via `main_with_a2a()` |
| Hermes | `python -m hermes.daemon` | `HermesDaemon.start()` via `main_with_a2a()` |
| ClawdBot | `python -m clawdbot.daemon` | `ClawdBotDaemon.start()` via `main_with_a2a()` |
| Perseus (legacy) | `python -m perseus.daemon` | `PerseusDaemon.start()` (refuses if OJ running) |

### Pipeline Entry
- **Task-queue mode:** Perseus scheduler inserts tasks -> `TitanDaemon._process_task_queue()` -> `TASK_HANDLERS[task_type]`
- **DAG mode:** `titan/workflow_pipeline.py` -> `run_pipeline()` -> `WorkflowEngine.run(graph)`
- **Direct A2A:** `shared/comms.py` -> `request_task()` / `delegate_task()` -> A2A endpoint

### A2A Handlers
| Agent | A2A File | Port |
|-------|----------|------|
| Orchestrator | `orchestrator.py` `handle_a2a()` | 9000 |
| Titan | `titan/a2a_server.py` `create_titan_a2a()` | 9001 |
| Hermes | `hermes/a2a_server.py` `create_hermes_a2a()` | 9002 |
| ClawdBot | `clawdbot/a2a_server.py` (inferred) | 9003 |
| Ruflo | External (claude-flow) | 9004 |

## soul/ Directory (Agent DNA Location)

The `soul/` directory contains behavioral files that the backprop engine can modify nightly.

| File | Purpose | Backprop Editable |
|------|---------|-------------------|
| `soul/soul_agent.md` | Agent behavioral rules, autonomy boundaries | Yes (except immutable sections) |
| `soul/soul_copy.md` | Copywriting guidelines, email tone, compliance rules | Yes (lines 34-42 are IMMUTABLE) |
| `soul/soul_hermes.md` | Hermes-specific personality and communication style | Yes |
| `soul/soul_values.md` | Core values and ethical guidelines | Yes |
| `soul/templates/` | Industry-specific outreach email templates | Yes |

**Agent DNA placement:** New personality/behavioral files go in `soul/`. The backprop engine (`openjarvis/vassals/backprop.py`) reads from and edits files in this directory. New Agent DNA research docs go in `intel/agent-dna/`. Implementation artifacts (behavioral configs, identity files) belong in `soul/`.

## Configuration Files

### Environment
- `.env` — LIVE API keys (DO NOT READ). Contains: Claude API, Telegram, Instantly, Firecrawl, Stripe, Wise, Recraft, Base RPC, Conway keystore password, etc.
- `CONWAY_KEYSTORE_PASSWORD` — required for wallet operations
- `BASE_RPC_URL` — Base L2 RPC endpoint
- `*_A2A_PORT` — A2A ports per daemon
- `USE_A2A_DISPATCH` — feature flag for A2A vs DB polling

### System Config (DB table: `system_config`)
Runtime key/value config shared by all daemons. Accessed via `shared/db.py` -> `get_config()` / `set_config()`. Examples:
- `titan_paused` — pause Titan processing
- `expansion_enabled` — enable revenue expansion
- `active_shadow_discovery_skill` — currently active shadow skill
- `preferred_discovery_skill` — adopted skill preference

### Docker Compose (`docker-compose.yaml`)
Services: Postgres, Qdrant (vector DB), Mem0, N8N (workflow automation)

### Build/Lint
- `pyproject.toml` or `setup.cfg` — Python project config
- `ruff` — linter (run via `make quality`)
- `pytest` — test runner

## Data Flow (Lead Lifecycle)

```
1. DISCOVER: lead_discovery.py
   - Uses skills (apify, firecrawl, web search) to find businesses
   - Inserts into `clients` table with status='discovered'
   - Stores source_campaign for attribution

2. RESEARCH: lead_research.py
   - Enriches leads via ClawdBot (browser, Firecrawl)
   - Updates `clients` with website_url, email, industry, research_data
   - Transitions status: discovered -> researched

3. COMPOSE: email_compose.py
   - Loads soul/soul_copy.md for guidelines
   - Uses installed skills (cold-email, etc.) or LLM directly
   - Creates personalized email based on research
   - Stores in email_drafts or directly on client record
   - Transitions status: researched -> email_ready

4. SEND: email_send.py
   - Sends via Instantly.ai API (tools/instantly_client.py)
   - Tracks in outreach_metrics table
   - Transitions status: email_ready -> contacted

5a. FOLLOW-UP: follow_up.py
   - Checks reply status via Instantly analytics
   - AI decides next action based on reply content
   - Transitions: contacted -> interested/rejected/follow_up

5b. SYNC ANALYTICS: email_send.py (sync_campaign_analytics)
   - Pulls opens, clicks, replies from Instantly
   - Updates outreach_metrics

6. CLOSE: close_deal.py
   - Handles interested leads: proposals, demos, negotiation
   - Transitions: interested -> demo_built -> proposal_sent -> negotiating -> closed

7. BUILD: build_site.py
   - Delegates to ClawdBot for site construction
   - Uses templates from templates/ directory
   - Transitions: closed -> building

8. DEPLOY: deploy_site.py
   - Netlify deployment via clawdbot/netlify_deploy.py
   - Custom domain setup via tools/domain_manager.py
   - Transitions: building -> deployed

9. INVOICE: invoice.py
   - Routes payment via tools/payment_router.py (Stripe + Wise)
   - Records in deals table
   - Transitions: deployed -> invoiced -> paid
```

## Where to Add New Code

### New Pipeline Stage
- Implementation: `titan/pipeline/{stage_name}.py`
- Register handler: `titan/daemon.py` `TASK_HANDLERS` dict
- Add to DAG: `titan/workflow_pipeline.py` `build_pipeline_graph()`
- Add task routing: `shared/task_routing.py` `TASK_ROUTING` dict
- Add schedule: `perseus/scheduler.py` `SCHEDULES` list

### New Daemon/Agent
- Create directory: `{agent_name}/`
- Create daemon: `{agent_name}/daemon.py` extending `AgentBase`
- Create A2A server: `{agent_name}/a2a_server.py`
- Register in orchestrator: `orchestrator.py` `VASSAL_CONFIG`
- Add routing: `shared/task_routing.py`
- Add to supervisor: `openjarvis/vassals/supervisor.py`

### New Skill
- Place in `.agent/skills/{skill_name}/`
- Must have TOML config (`skill.toml` or similar)
- Loaded by `shared/skill_loader.py`

### New Tool Integration
- Implementation: `tools/{service_name}_client.py`
- Add env vars to `.env` (via operator)
- Import from pipeline stages as needed

### New Soul/Behavioral File
- Place in `soul/{filename}.md`
- Will be read by pipeline stages (e.g., email_compose loads soul_copy.md)
- Editable by backprop engine unless added to `IMMUTABLE_FILES`

### New Intel Research
- Place in `intel/{topic}/`
- Reference from `.planning/` docs for integration planning

### New Tests
- Place in `tests/test_{module}.py`
- Run with `PYTHONPATH=. python3 -m pytest tests/ -v`

## Special Directories

### `logs/`
- Purpose: Daemon log files (perseus.log, titan.log, hermes.log, clawdbot.log)
- Generated: Yes (at runtime)
- Committed: No

### `.agent/skills/`
- Purpose: Installed skill packages for ClawdBot execution
- Generated: No (installed manually or via skill manager)
- Committed: Partially (large collections may be gitignored)

### `conway/data/keystores/`
- Purpose: Encrypted agent wallet keystores
- Generated: Yes (by WalletManager)
- Committed: No (contains encrypted private keys)

### `intel/`
- Purpose: Research documents for future integration
- Generated: No (written by operator/research sessions)
- Committed: Yes

### `.planning/`
- Purpose: GSD planning and codebase analysis documents
- Generated: Yes (by /gsd commands)
- Committed: Yes

---

*Structure analysis: 2026-03-29*
