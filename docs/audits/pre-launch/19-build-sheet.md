# Perseus — Technical Build Sheet

**Classification:** CONFIDENTIAL — TECHNICAL DUE DILIGENCE
**Version:** Pre-launch (Phase 42.5 v2 in progress)
**Generated:** 2026-04-07
**Prepared for:** Buyer Technical Team
**Prepared by:** Pre-Launch Audit Pass (Perseus Internal)

---

## 1. Executive Summary

Perseus is a privately operated, autonomous multi-agent platform that runs a full-stack services business — client acquisition, website production, hosting, and ongoing customer operations — with minimal human intervention. The system is deployed as eight native daemons and a small containerised infrastructure layer on a single Apple Mac Studio (M4 Max, 14-core CPU / 32-core GPU / 36 GB unified memory / 512 GB SSD). LLM inference is hybrid: hot-path models run locally via MLX / Ollama for cost control and latency, while heavier reasoning, vision, and fallback calls are routed through LiteLLM to Anthropic, OpenRouter, and other commercial providers. The platform has been under active development for roughly 18 months and is currently undergoing Phase 42.5 v2 — a provider-agnostic routing and local-tier cutover that is wired in code but not yet connected to the live daemons.

This document is a neutral snapshot of what is in the repository at the audit commit. It is intended for a buyer's technical team performing due diligence. Security findings from the concurrent audit pass are surfaced in Section 7 and should be read in conjunction with `docs/audits/pre-launch/03-cso.md` and `docs/audits/pre-launch/11-tech-debt.md`.

---

## 2. System Architecture Overview

### 2.1 Topology

Perseus is a **daemon-per-agent** architecture. Each agent is a long-running Python process with its own event loop, its own FastAPI control surface on a fixed local port, and its own SOUL definition (system prompt + capability manifest). Cross-daemon communication uses three channels:

- **A2A (agent-to-agent) HTTP** — direct REST between daemon ports
- **Postgres as event bus** — the `events` and `task_queue` tables are polled by each daemon
- **Shared Postgres state** — canonical business data (clients, deals, subscriptions) lives in Postgres 16

### 2.2 The 8 Daemons

| # | Daemon | Role | Port | Primary Loop |
|---|--------|------|------|-------------|
| 1 | **Perseus** | Scheduler / orchestrator / health sentinel | 9000 | Cron-like task dispatch, sleep cycles, backprop |
| 2 | **Titan** | Revenue engine — 10-stage client acquisition pipeline | 9001 | State machine advancing deals from lead → paid |
| 3 | **Hermes** | Alerts, War Room dashboard, Telegram bot, Jarvis UI | 9002 / 8500 | Telegram long-poll + dashboard API |
| 4 | **Clawdbot** | Site builder, browser automation, visual production | 9003 | Job queue consuming build requests |
| 5 | **Conway** | Agent economics, Base L2 USDC wallets, shared library | (library) | Ledger + wallet RPC |
| 6 | **Deerflow** | Research agent, source scoring, brief generation | (library) | Research cycles |
| 7 | **Ruflo** | Code-fixing swarm, Aider-based edit loops | 9004 | Fix task queue |
| 8 | **OpenJarvis** | Workflow engine, DAG orchestration framework | (library) | WorkflowEngine DAG executor |

### 2.3 Native Services (non-containerised, macOS host)

- **Ollama** on `:11434` — local LLM server for qwen2.5, nomic-embed-text
- **MLX hot set** (Phase 42.5 v2) — Qwen3-30B-A3B, Qwen3-Coder-14B, Qwen3-8B, Qwen-VL-7B/32B, Parakeet (ASR), Kokoro (TTS), embeddings
- **AirLLM Llama-70B** — heavy-tier inference on-device
- **Draw Things** — image generation client
- **launchd** — process supervision via `scripts/install-launchagents.sh`

### 2.4 Containerised Infrastructure (docker-compose)

| Service | Image | Purpose | Port |
|--------|-------|---------|------|
| postgres | `postgres:16.2-alpine` | Canonical state store | 5432 |
| qdrant | `qdrant/qdrant:v1.8.4` | Vector DB for Mem0 | 6333 |
| mem0 | local build (`Dockerfile.mem0_compat`) | Semantic memory service | 8888 |
| n8n | `n8nio/n8n:1.30.1` | Workflow automation for external triggers | 5678 |

All four containers have healthchecks and are memory-capped (384–512 MB). CrewAI and Pinchtab were present in earlier versions and have been explicitly removed.

### 2.5 Data Flow (primary acquisition pipeline)

1. Deerflow discovers prospects → writes to `clients` table
2. Titan state machine advances lead through 10 stages → updates `deals`
3. Hermes emits human-review alerts via Telegram when `review_queue` hits thresholds
4. Clawdbot builds the client site (visual pipeline) → writes artifacts, updates `site_health`
5. Conway assigns wallet, bills USDC on Base → writes to ledger
6. Perseus scheduler runs sleep-cycle consolidation, backprop weighting, and cell-division (agent spawning) decisions nightly

---

## 3. Technology Stack

### 3.1 Core

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Runtime | Python | 3.11+ | All daemons |
| Web framework | FastAPI | ≥0.115.0 | Daemon control surfaces, dashboard API |
| ASGI server | uvicorn | ≥0.32.0 | FastAPI host |
| Templates | Jinja2 | ≥3.1.0 | Dashboard + email templates |
| HTTP client | httpx | ≥0.27.0 | Async cross-daemon calls |
| HTTP client | requests | ≥2.31.0 | Sync integrations |
| DB driver | psycopg (binary) | ≥3.1.0 | Postgres access |
| DB pooling | psycopg-pool | ≥3.2.0 | Connection pools |
| Config | python-dotenv | ≥1.0.0 | Env loading |
| Telegram | python-telegram-bot | ≥21.0 | Hermes bot |
| Multipart | python-multipart | ≥0.0.9 | Form uploads |

### 3.2 Data & Infrastructure

| Component | Technology | Version | Notes |
|-----------|-----------|---------|-------|
| RDBMS | Postgres | 16.2-alpine | 22 canonical tables |
| Vector DB | Qdrant | 1.8.4 | Used by Mem0 |
| Semantic memory | Mem0 | custom build | Backed by Qdrant + sqlite history |
| Workflow engine | n8n | 1.30.1 | Basic-auth protected |
| Embeddings (dev) | nomic-embed-text (Ollama) | — | 768-dim |
| LLM gateway | LiteLLM | — | Phase 42.5 cutover component |

### 3.3 Tooling & Quality

| Tool | Version | Purpose |
|------|---------|---------|
| pytest | ≥8.0.0 | Unit + async tests |
| pytest-asyncio | ≥0.24.0 | Async test support |
| ruff | ≥0.11.0 | Linting + formatting (line length 100, py311 target) |
| mypy | ≥1.11.0 | Strict type checking across `shared`, `perseus`, `titan`, `hermes`, `clawdbot` |
| types-requests | ≥2.32.0 | Type stubs |

### 3.4 Notable Absences

- `anthropic` SDK is **commented out** in `requirements.txt` — Perseus relies on Claude Max / Claude Code as the primary Anthropic access path rather than direct SDK calls
- No ORM (psycopg raw queries throughout)
- No Kubernetes, Helm, or cloud orchestration — single-host deployment

---

## 4. Dependency Tree

The repository uses two dependency pins:

- **`requirements.txt`** — flat, 12 runtime deps + 4 test/lint deps. All constraints are `>=` (minimum bound only), no upper bounds.
- **`uv.lock`** — transitive lock via `uv`, present in the repo.

### 4.1 Dependency risk observations

- **No upper bounds** on any dependency. A breaking upstream release could land on a fresh install. Acceptable for a single-operator system; would need pinning for multi-environment reproducibility.
- **Exactly-pinned containers** (postgres 16.2, qdrant 1.8.4, n8n 1.30.1) — good.
- **Phase 42.5 v2 introduces new dependencies** (Aider, MLX, AirLLM, Kokoro, Parakeet) which are **not yet declared** in `requirements.txt`. They are assumed to be installed out-of-band via `scripts/install-openclaw.sh` and the native-services setup runbook.

---

## 5. Build, Test, Deploy Procedures

### 5.1 Build

The system does not produce build artifacts in the traditional sense. "Build" means:

1. `scripts/setup-perseus.sh` — one-shot environment bootstrap
2. `pip install -r requirements.txt` inside a venv (or `uv sync` via the lockfile)
3. `docker compose up -d postgres qdrant mem0 n8n`
4. `psql` runs `scripts/init-db.sql` automatically on first postgres start
5. `scripts/install-launchagents.sh` registers the daemons with launchd

### 5.2 Test

- Test root: `tests/`, configured via `pyproject.toml` (`asyncio_mode = "auto"`)
- Strict mypy config on four daemon packages — warn_unused_ignores, warn_unreachable, strict_equality
- `make test` wraps pytest — see `Makefile`
- A CI hardening pass was committed recently (`e8e6026 Fix CI portability and quality gates`)
- **Test coverage is not measured in CI** and no coverage badge is present. Depth of the test suite cannot be inferred from file count alone.

### 5.3 Deploy

- **Start:** `scripts/start-perseus.sh`
- **Stop:** `scripts/stop-perseus.sh`
- **Health:** `scripts/health-check.sh`
- **Backup:** `scripts/backup-postgres.sh` (and `restore-from-backup.sh`)
- **Migrations:** `scripts/migrations/` directory + `migrate_to_litellm.py` one-shots
- Deployment target is a **single physical host** (the Mac Studio). There is no staging environment, no blue/green, no canary mechanism.

---

## 6. External Service Dependencies

| Provider | Role | Criticality | Notes |
|----------|------|-------------|-------|
| Anthropic (Claude) | Primary reasoning LLM | High | Accessed via Claude Max / Claude Code, not direct SDK |
| OpenRouter | Multi-provider LLM gateway | High | LiteLLM fallback / model variety |
| Kimi K2.5 | Cloud vision model | Medium | Used by Clawdbot for heavy visual tasks |
| Recraft | Image generation | Medium | Alternative to Draw Things |
| Telegram Bot API | Operator notifications + Jarvis UI | High | Hermes relies on it |
| Base L2 (USDC) | Payment rails | High | Conway wallet operations |
| Ollama (local) | Fast LLM inference | High | On-host, no network dep |
| Draw Things (local) | Image gen | Medium | On-host |

Perseus assumes reliable connectivity for Anthropic and OpenRouter. There is fallback logic between providers via LiteLLM, but no documented graceful-degradation mode if all cloud LLMs are unavailable simultaneously. The MLX hot set in Phase 42.5 v2 is the intended answer to this, but is not yet wired to the daemons.

---

## 7. Data Model

### 7.1 Schema summary

`scripts/init-db.sql` defines **22 tables** across five domains:

**Client & revenue (6 tables)**
`clients`, `deals`, `hosting_subscriptions`, `receptionist_subscriptions`, `outreach_metrics`, `budget_tracking`

**Operations (5 tables)**
`budget_recurring_costs`, `site_health`, `activity_log`, `task_queue`, `events`

**Review & config (3 tables)**
`review_queue`, `system_config`, `agent_registry`

**Agent learning (5 tables)**
`titan_learnings`, `titan_rules`, `revenue_expansion_opportunities`, `agent_decisions`, `sleep_cycle_log`

**Outbound (3 tables)**
`email_sequences`, `outbound_email_log`, `training_data`

All tables use `IF NOT EXISTS`, so the init script is idempotent. Indexes, foreign keys, and constraints are inlined with each table definition; see `scripts/init-db.sql` lines 1–555 for the authoritative schema.

### 7.2 Data model observations

- **Postgres is the event bus** — `events` and `task_queue` are polled by daemons. This is simple and observable but limits horizontal scaling.
- **No migration tool** like Alembic. Schema evolution is managed by hand-written scripts in `scripts/migrations/`.
- **`training_data` table** surfaces the learning loop: agent outcomes feed back into future decisions (see `titan_learnings`, `agent_decisions`, `sleep_cycle_log`).

---

## 8. Security Posture

### 8.1 Concurrent audit findings (surfaced from this pass)

The following are from the in-progress audit. They are **unresolved at the time of this build sheet** and should be treated as open risks:

| # | Finding | Severity | Location |
|---|---------|----------|----------|
| S1 | **Unsalted SHA-256 key derivation** in the escalation log redactor. A single rainbow-table lookup or prefix-match attack could reverse redacted identifiers. | High | `shared/escalation_log/redactor.py` |
| S2 | **Decorative sandbox.** `litellm/sandboxes/verifier.sb` contains a macOS seatbelt policy file but no enforcement path wires it to actual subprocess execution. The sandbox exists as a file but does not constrain any runtime. | High | `litellm/sandboxes/verifier.sb` |
| S3 | **15 missing environment variables.** The audit enumerates 15 `os.getenv` calls that have no corresponding entry in `.env.example` or default. Several gate security-relevant behavior (alert webhooks, API keys). | High | cross-repo |

Full detail: `docs/audits/pre-launch/03-cso.md`.

### 8.2 Standing posture

- **Secrets management:** `.env` file, `python-dotenv` loader. No Vault, no KMS, no per-daemon isolation of credentials. All daemons read from the same env.
- **Default passwords in docker-compose:** `perseus_secure_2026` for Postgres and `perseus_n8n_2026` for n8n are committed as fallback defaults. These are overridable by env but represent an exposure if env is not set.
- **No TLS between daemons.** A2A traffic is plain HTTP on localhost, which is acceptable given the single-host model but precludes any network-distributed deployment without rework.
- **Prompt injection defense:** Not formally enumerated. Agents consume LLM outputs and write to Postgres; no input-validation layer on LLM → action boundaries is documented.
- **Bot token (Telegram):** read from env, not rotated automatically.
- **Wallet private keys (Conway / Base L2):** stored under `conway/knowledge/` per memory index. Handling mechanism not audited in this pass — recommend Section 8 follow-up.

### 8.3 Access control

- Dashboard (Hermes, port 8500) exposes a Jarvis UI with no documented auth layer in the repo overview. n8n uses HTTP basic auth.
- There is no multi-user model. The platform is single-operator by design.

---

## 9. Operational Runbooks Index

Location: `docs/runbooks/`

| Runbook | Purpose |
|---------|---------|
| `aider-pattern-guide.md` | Aider architect+editor pattern used by Ruflo/Clawdbot |
| `cutover-playbook.md` | Phase 42.5 cutover procedure |
| `image-gen-routing.md` | Draw Things / Recraft / Kimi K2.5 routing rules |
| `local-tier-rollback.md` | Rollback from MLX local tier to cloud |
| `native-services-setup.md` | MLX, Ollama, Parakeet, Kokoro install |
| `voice-loop-guide.md` | Voice input/output pipeline (Parakeet → router → Kokoro) |

Additional operational reference: `HANDOFF.md` at repo root, `ROADMAP.md`, `docs/ARCHITECTURE.md`, `docs/RESEARCH.md`.

---

## 10. Known Technical Debt

Authoritative source: `docs/audits/pre-launch/11-tech-debt.md` (222 lines, generated in this audit pass).

Summary of the high-signal items from that file:

- Unpinned Python dependencies (Section 4.1 above)
- Strict mypy enabled but several modules carry `# type: ignore` escape hatches
- No integration tests covering cross-daemon A2A paths
- `tmp_check_*.py` and `tmp_inspect_sites.py` at repo root indicate ad-hoc debug scripts that should be moved or removed
- Phase 42.5 v2 ghost integration (see Section 11)
- Event bus is Postgres polling — a queue system (NATS / Redis Streams) is on the roadmap
- No formal SLO / error budget definitions
- Dashboard (Hermes War Room) auth layer undocumented

Refer to `11-tech-debt.md` for full remediation priorities, owner assignments, and estimated effort.

---

## 11. Phase 42.5 v2 — Ghost Integration Risk

Phase 42.5 v2 is the current active development phase. It introduces a provider-agnostic LLM routing layer, a local multi-model MLX hot set, an Aider-based architect+editor loop, voice I/O, and image generation routing. It is the cutover that removes Anthropic lock-in.

### 11.1 What landed

Approximately **50 new files** across these directories, all committed (commit `30770c0`):

- `litellm/` — new top-level package, LiteLLM gateway + sandboxes
- `shared/aider/` — `clawdbot_loop.py`, `ruflo_loop.py` (architect+editor pattern)
- `shared/escalation_log/` — redactor (see S1 above)
- `shared/imagegen/draw_things_client.py`
- `shared/verifier/` — consistency, depth guard, grammar compiler, A2A callback
- `shared/voice/` — Parakeet (ASR), Kokoro (TTS), intent router
- `shared/semantic_cache.py`, `shared/tier_classifier.py`, `shared/tiers.py`
- `scripts/migrate_to_litellm.py`, `scripts/rollback_litellm.py`
- `docs/runbooks/` — six Phase 42.5 runbooks
- Native-services install script (`install-openclaw.sh`)

### 11.2 The ghost integration problem

**Buyer-critical observation:** the 50 files exist in the tree and pass static checks, but the eight daemons (`perseus/`, `titan/`, `hermes/`, `clawdbot/`) **do not yet import the new modules in their runtime loops**. Specifically:

- `shared/llm_client.py` is still the path daemons call for LLM work
- The LiteLLM gateway in `litellm/` has no daemon-side import
- The Aider loops in `shared/aider/` are implemented but not invoked from Ruflo or Clawdbot daemons
- Voice and verifier modules are importable but unwired
- `scripts/migrate_to_litellm.py` exists but has not been run against the live daemons

In practical terms: if a buyer clones the repo and starts the system today, they get the pre-42.5 daemons calling Anthropic via the existing client. The new routing layer, local models, and verifier framework are present as code but inert at runtime. The cutover work remaining is the wiring itself — not greenfield design.

### 11.3 Why this matters for diligence

- The repository can give a false impression of Phase 42.5 v2 completion. The **commit count and file count do not reflect runtime state.**
- The rollback runbook (`local-tier-rollback.md`) is forward-looking — there is nothing to roll back from yet.
- Independent verification: run the system and confirm which LLM endpoint the daemons actually call. A grep for `litellm` imports inside `perseus/`, `titan/`, `hermes/`, and `clawdbot/` should return zero hits today.

---

## 12. Deployment & Infrastructure Summary

| Attribute | Value |
|-----------|-------|
| Hosting model | Single physical Mac Studio M4 Max, on-premise |
| OS | macOS (Darwin 24.6.0 observed) |
| Process supervision | launchd via installed agents |
| Container runtime | Docker Desktop for Mac |
| Scaling model | Vertical only — no horizontal scaling design |
| Environments | Production only (no staging) |
| CI/CD | GitHub workflows under `.github/` — CI hardened in commit `e8e6026` |
| Backup | Postgres dump via `backup-postgres.sh`, destination not enumerated here |
| Monitoring | Hermes War Room dashboard (port 8500), Telegram alerts |
| Log aggregation | `shared/logging_config.py`, per-daemon files |

---

## 13. Risk & Dependency Register

| Risk | Severity | Likelihood | Mitigation present? |
|------|----------|-----------|---------------------|
| Single-host failure (Mac Studio hardware) | Critical | Low | Postgres backup only; no standby host |
| Anthropic API outage | High | Medium | Phase 42.5 v2 local tier (not yet wired) |
| Unsalted SHA-256 redactor (S1) | High | High if exposed | None — unresolved |
| Decorative sandbox (S2) | High | High | None — unresolved |
| 15 missing env vars (S3) | High | High | None — unresolved |
| Unpinned deps | Medium | Medium | uv.lock partial mitigation |
| Default compose passwords | Medium | Low | Env overridable |
| No staging environment | Medium | High | Manual rollback playbook only |
| Ghost-integrated Phase 42.5 v2 | Medium | Certain | Cutover work scheduled |
| No multi-user access control | Low | N/A by design | Acceptable for single operator |

---

## 14. Cost Structure (inferred from code)

Perseus's cost model is deliberately engineered to push inference on-host:

- **Local inference (MLX / Ollama):** zero marginal cost, bounded by electricity and hardware amortisation
- **Cloud LLMs:** metered via LiteLLM (`shared/spend_alerts.py`, `scripts/show_spend.py`, `scripts/show_cache_stats.py`). The presence of spend alerts and cache stats tooling implies active cost tracking.
- **Infrastructure:** Docker containers on-host — no cloud compute spend
- **Payment rails:** USDC on Base L2 (gas fees only)
- **External APIs:** Telegram (free), Recraft / Kimi K2.5 (metered)

Exact burn is not determinable from static analysis. The operator has tooling (`show_spend.py`) to answer this in the live system.

---

## 15. Development Status & Maturity Assessment

| Dimension | Status |
|-----------|--------|
| Overall maturity | Late-Beta / Pre-launch |
| Production readiness | Blocked on Phase 42.5 v2 cutover + three open security findings |
| Documentation | Reasonable — architecture doc, runbooks, handoff file, audit suite |
| Test coverage | Present but unmeasured |
| CI/CD | Basic — recently hardened |
| Observability | Dashboard + Telegram alerts; no external APM |

Per operator policy stated in the memory index: **"Perseus does not launch until Phase 42.5 is fully cutover. 6–8 weeks total."** Quality is explicitly prioritised over launch date.

---

## 16. Appendix — Top-Level File Tree

```
/
├── Makefile                           Build entrypoints (test, lint, start, stop)
├── README.md                          Project overview
├── ROADMAP.md                         Phase plan reference
├── HANDOFF.md                         Operator session handoff
├── LICENSE
├── pyproject.toml                     Ruff + mypy + pytest config
├── requirements.txt                   Runtime pins (12 + 4)
├── uv.lock                            Transitive lock
├── docker-compose.yaml                4 services: postgres, qdrant, mem0, n8n
├── perseus/                           Scheduler daemon (daemon.py, scheduler.py, sleep_cycle.py, backprop.py, cell_division.py, health.py, agent_registry.py)
├── titan/                             Revenue engine (daemon.py, pipeline/, state_machine.py, compliance.py, training.py, review_mode.py, expansion.py, deliverability.py, memory.py)
├── hermes/                            Alerts + War Room (daemon.py, alerts.py, telegram_bot.py, web/, skills/)
├── clawdbot/                          Site builder (daemon.py, brain.py, site_builder.py, capabilities.py, capability_resolver.py, design_sources.py, safety.py)
├── shared/                            Cross-daemon library — Phase 42.5 v2 hot zone
│   ├── llm_client.py                  Legacy LLM client (still live)
│   ├── db.py                          Postgres access
│   ├── config.py                      Env loader
│   ├── agent_base.py                  Base class
│   ├── comms.py                       A2A HTTP
│   ├── execution_loop.py              Daemon main loop
│   ├── self_model.py
│   ├── semantic_cache.py              [Phase 42.5] Unwired
│   ├── tier_classifier.py             [Phase 42.5] Unwired
│   ├── tiers.py                       [Phase 42.5] Unwired
│   ├── skill_loader.py
│   ├── lead_worker.py
│   ├── logging_config.py
│   ├── pipeline_alerts.py
│   ├── spend_alerts.py
│   ├── aider/                         [Phase 42.5] Architect+editor loops (unwired)
│   ├── escalation_log/                [Phase 42.5] Redactor — S1 finding
│   ├── imagegen/                      [Phase 42.5] Draw Things client (unwired)
│   ├── verifier/                      [Phase 42.5] Consistency, depth guard, grammar, a2a (unwired)
│   └── voice/                         [Phase 42.5] Parakeet, Kokoro, router (unwired)
├── litellm/                           [Phase 42.5] Gateway + sandbox — S2 finding
├── config/                            Runtime config
├── scripts/                           Setup, migration, launchagents, backup, init-db.sql (22 tables, 555 lines)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── RESEARCH.md
│   ├── runbooks/                      6 Phase 42.5 runbooks
│   └── audits/pre-launch/             This audit pass (19 files)
├── tests/                             pytest + asyncio
├── tools/                             Third-party tool wrappers (browser-use, firecrawl)
├── soul/                              SOUL files per agent
├── templates/
├── tmp_check_client_sites.py          Ad-hoc debug (tech debt)
├── tmp_check_tables.py                Ad-hoc debug (tech debt)
└── tmp_inspect_sites.py               Ad-hoc debug (tech debt)
```

---

## 17. Recommendations for the Buyer's Technical Team

These are neutral next steps for a diligence follow-up:

1. **Verify Phase 42.5 v2 runtime state** — confirm by observation that daemons call the legacy `shared/llm_client.py` rather than the new `litellm/` gateway. Grep for `import litellm` inside daemon packages.
2. **Reproduce the three open security findings** (S1/S2/S3) and determine if they are pre-launch blockers for the acquirer.
3. **Quantify test coverage** — run pytest with `--cov` and review the numeric result. Static inspection cannot infer depth.
4. **Red-team the escalation log redactor** — the SHA-256 finding should be trivially demonstrable.
5. **Review wallet key handling** (`conway/` — not deeply inspected in this pass).
6. **Request a live demo** of a full acquisition pipeline run (Titan stage 1 → 10) to confirm the business loop actually closes end-to-end on the current commit.
7. **Confirm hardware ownership and succession plan** — the single-host model ties the platform to physical hardware.

---

*End of Build Sheet. Prepared under the Perseus pre-launch audit pass. For correction requests or follow-up, see the audit index at `docs/audits/pre-launch/`.*
