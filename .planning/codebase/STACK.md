# Technology Stack

**Analysis Date:** 2026-03-29

## Languages

**Primary:**
- Python 3.11+ — All daemon code, orchestrator, pipeline, tools, shared libraries
- Rust — OpenJarvis framework core (`rust/crates/` workspace with 17 crates via `rust/Cargo.toml`)
- TypeScript 5.7.3 — War Room frontend (`hermes/web/frontend/`)

**Secondary:**
- SQL — Postgres schema and migrations (`scripts/init-db.sql`, `scripts/migrations/`)
- Bash — Operational scripts (`scripts/start-perseus.sh`, `scripts/health-check.sh`)
- TOML — Skill definitions, config (`.agent/skills/`)

## Runtime

**Environment:**
- Python 3.11+ (`pyproject.toml` line 10: `requires-python = ">=3.11"`)
- Rust workspace resolver 2 (`rust/Cargo.toml`)
- Node.js 22 (in sandbox Dockerfile: `deploy/docker/Dockerfile.sandbox`)
- Ollama >= 0.6.2 (local LLM with TurboQuant KV cache compression)

**Package Managers:**
- pip with hatchling build backend + uv for production builds (`deploy/docker/Dockerfile`)
- pnpm (frontend in `hermes/web/frontend/`)
- Cargo (Rust workspace)
- Lockfile: `rust/Cargo.lock` present; no Python lockfile (relies on `pyproject.toml` version ranges + `requirements.txt`)

## Frameworks

**Core Backend:**
- OpenJarvis — Custom orchestrator (Rust core + Python bindings via PyO3/maturin)
  - `openjarvis/` Python package + `rust/crates/openjarvis-python/` bindings
  - Provides: WorkflowEngine, A2A protocol, EventBus, AgentManager, CapabilityPolicy, TraceStore, AuditLogger
- FastAPI >= 0.110 — Dashboard API and A2A HTTP endpoints (`hermes/web/app.py`)
- psycopg v3 (async) + psycopg-pool — Postgres access (`shared/db.py`)
- Pydantic >= 2.0 — Data validation
- Jinja2 >= 3.1 — Server-side templates

**Frontend:**
- Next.js 16.2.0 — React framework for War Room (`hermes/web/frontend/`)
- React 19.2.4, Tailwind CSS 4.2.0, Radix UI, shadcn/ui pattern
- Recharts, @tanstack/react-table, Three.js/R3F for 3D visuals

**Testing:**
- pytest >= 8 — Test runner (asyncio_mode: `auto`)
- pytest-asyncio >= 0.24, pytest-cov >= 5, respx >= 0.22

**Linting/Quality:**
- ruff >= 0.4 — target py311, line-length 100, rules: E, F, I, B, UP
- mypy >= 1.11 — packages: shared, titan, hermes, clawdbot, tools, conway, perseus
- pre-commit >= 3.0

**Build/Dev:**
- hatchling — Python build backend
- maturin >= 1.12.6 — Rust-Python bridge builder
- Make/just — Standardized commands

## Key Dependencies

**Critical (revenue path):**
- `httpx >= 0.27` — All async HTTP (Claude API, Ollama, Stripe, Wise, Instantly, Firecrawl, Recraft)
- `psycopg[binary] >= 3.1` + `psycopg-pool >= 3.2` — Async Postgres with connection pooling
- `python-telegram-bot >= 22.6` — Alert dispatch (`hermes/telegram_bot.py`)
- `openai >= 1.30` — Ollama OpenAI-compatible endpoint access

**Infrastructure:**
- `python-dotenv >= 1.0.0` — Env config loading (`shared/config.py`)
- `prometheus-client >= 0.24.1` — Metrics (`shared/observability.py`)
- `sentry-sdk >= 2.55.0` — Error tracking (optional, graceful no-op)
- `rich >= 13`, `click >= 8` — CLI

**Optional Extensions (declared in `pyproject.toml`):**
- `inference-mlx`: MLX-LM for Apple Silicon local inference
- `inference-vllm`: vLLM for GPU inference
- `inference-cloud`: Anthropic + OpenAI SDKs
- `inference-google`: Google GenAI SDK
- `inference-litellm`: LiteLLM unified routing
- `memory-graph`: Neo4j driver
- `memory-faiss`: FAISS + sentence-transformers
- `memory-colbert`: ColBERT + PyTorch
- `memory-bm25`: BM25 ranking
- `memory-pdf`: pdfplumber for PDF ingestion
- `browser`: Playwright for automation
- `scheduler`: croniter for cron expressions
- `security-signing`: cryptography for Ed25519 agent signing
- `sandbox-wasm`: wasmtime for WASM sandboxing
- `sandbox-docker`: Docker SDK for container sandboxing
- `speech`: faster-whisper for STT
- `orchestrator-training`: PyTorch + Transformers for LoRA/GRPO
- `learning-dspy`: DSPy prompt optimization
- `eval-wandb`: W&B for eval tracking

**Rust Workspace Key Dependencies (`rust/Cargo.toml`):**
- `tokio 1` (full) — Async runtime
- `reqwest 0.12` — HTTP client
- `rusqlite 0.32` — SQLite (traces, audit, agent state)
- `pyo3 0.23` — Python bindings
- `ed25519-dalek 2` — Cryptographic signing (agent identity)
- `rig-core 0.31` — AI agent framework
- `schemars 1` — JSON schema generation
- `sha2 0.10` — Hashing

## Configuration

**Environment:**
- All config loaded from `.env` via `python-dotenv` in `shared/config.py`
- Central `PerseusConfig` singleton with 16 frozen dataclass sub-configs
- Dashboard overrides in Postgres `system_config` table (checked first via `shared/db.get_config()`)
- Feature flags: `MAGMA_ENABLED`, `CONWAY_ENABLED`, `RUFLO_ENABLED`, `SEAL_DIRECTIVES`, `INFERENCE_OPTIMIZATION`, `USE_A2A_DISPATCH`

**Required env vars (minimum viable):**
- `POSTGRES_PASSWORD` — Database
- `ANTHROPIC_API_KEY` — Claude API (or falls back to Ollama-only)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Alerts
- `DASHBOARD_SECRET` — Web auth

**Required for revenue pipeline:**
- `INSTANTLY_API_KEY` — Email campaigns
- `STRIPE_API_KEY` — Payment collection
- `NETLIFY_AUTH_TOKEN` — Site deployment
- `RECRAFT_API_KEY` — Image generation

## LLM Stack

**Cloud (Claude API via Anthropic):**
- Genius: `claude-opus-4` — Orchestration, complex reasoning
- Smart: `claude-sonnet-4-6` — Proposals, strategy
- Fast: `claude-haiku-4-5-20251001` — Primary workhorse
- Budget-gated: auto-downgrades at 80% of `MONTHLY_BUDGET_CAP` ($800 default)
- Client: raw httpx to `https://api.anthropic.com/v1/messages` (no Anthropic SDK)

**Local (Ollama):**
- Primary: `qwen2.5:14b-instruct-q4_K_M`
- Secondary: `llama3.2:3b` (classification)
- Embeddings: `nomic-embed-text` (768-dim, used by Mem0/Qdrant)
- TurboQuant KV cache: `turbo4` default (3.8x memory reduction)
- Flash attention: enabled by default

## Infrastructure (Docker Compose)

**Services (`docker-compose.yaml`):**
| Service | Image | Port | Memory | Purpose |
|---------|-------|------|--------|---------|
| Postgres | postgres:16.2-alpine | 5432 | 384MB | Primary database (23+ tables) |
| Qdrant | qdrant/qdrant:v1.8.4 | 6333 | 512MB | Vector database for embeddings |
| Mem0 | Custom (`tools/Dockerfile.mem0_compat`) | 8888 | 512MB | Semantic memory (Qdrant + Ollama) |
| Neo4j | neo4j:5-community | 7474/7687 | 512MB | Graph database (APOC plugin) |
| N8N | n8nio/n8n:1.30.1 | 5678 | 512MB | Workflow automation |
| Prometheus | prom/prometheus:v2.54.1 | 9090 | 256MB | Metrics collection |
| Grafana | grafana/grafana-oss:11.2.0 | 3001 | 256MB | Dashboards |

**Network:** `perseus-net` bridge. Total memory: ~2.9GB for Docker services.

**Deployment Dockerfiles (`deploy/docker/`):**
- `Dockerfile` — Standard (multi-stage, Python 3.12, uv pip install)
- `Dockerfile.gpu` — NVIDIA GPU
- `Dockerfile.gpu.rocm` — AMD ROCm GPU
- `Dockerfile.sandbox` — Sandboxed agent execution (Python 3.12 + Node.js 22)

## Dependency Health

**Issues:**
- No Python lockfile — `requirements.txt` and `pyproject.toml` have version divergence (e.g., fastapi >= 0.115 vs >= 0.110)
- `requests` used synchronously in `tools/firecrawl_client.py` while rest of codebase uses async `httpx` — inconsistent HTTP client
- `mem0ai==1.0.6` pinned only in `tools/Dockerfile.mem0_compat`, not in project deps
- `openai` package listed as dependency but only used for Ollama OpenAI-compat, not actual OpenAI calls

## Intel Integration Dependencies

**What exists vs what each intel reference needs:**

| Intel Reference | Code Injection Point | Existing Deps | Missing Deps |
|----------------|---------------------|---------------|-------------|
| **Agent DNA** (`intel/agent-dna/`) | `shared/llm_client.py` `system` param in `generate()` | httpx, config.py | None — injects via system prompt string |
| **Recursive Language Models** (`intel/recursive-language-models/`) | `shared/magma.py` (4-graph memory), `titan/memory.py` (Mem0 read/write) | Qdrant (Docker), Mem0 (Docker), Neo4j (Docker), nomic-embed-text | None — multi-graph retrieval + vector store already wired |
| **HyperAgents** (`intel/hyperagents/`) | `deploy/docker/Dockerfile.sandbox`, OpenJarvis agent lifecycle | Docker infra, wasmtime (optional extra) | `docker >= 7.0` SDK not installed by default; no agent isolation runtime active |
| **Post-Quantum Crypto** (`intel/post-quantum-crypto/`) | `rust/crates/openjarvis-security/` (ed25519-dalek), `conway/wallet.py` | `ed25519-dalek 2`, `cryptography >= 43` (optional) | `liboqs-python` or `pqcrypto` — **completely absent** from all deps |
| **DeerFlow** (`intel/deerflow/`) | `shared/pipeline_dag.py`, OpenJarvis WorkflowEngine | DAG runner, A2A protocol | None — existing engine supports the pattern |
| **Anti-Slop** (`intel/anti-slop/`) | `shared/llm_client.py` system prompts, `shared/skill_loader.py` | LLM client | None — quality constraints inject via system prompts |
| **Quantum Computing** (`intel/quantum-computing/`) | No direct code integration point | None | Everything — research-only, no implementation path |

---

*Stack analysis: 2026-03-29*
