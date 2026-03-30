# Technology Stack

**Analysis Date:** 2026-03-27

## Languages

**Primary:**
- Python 3.11+ - All backend daemons, orchestrator, pipeline, tools, shared libraries
- TypeScript 5.7.3 - War Room frontend (`hermes/web/frontend/`)

**Secondary:**
- SQL - Database schema and migrations (`scripts/init-db.sql`, `scripts/migrations/`)
- Bash - Operational scripts (`scripts/start-perseus.sh`, `scripts/stop-perseus.sh`, `scripts/health-check.sh`)

## Runtime

**Environment:**
- Python 3.11+ (target version in `pyproject.toml` line 10: `requires-python = ">=3.11"`)
- Node.js (for frontend, version not pinned; no `.nvmrc` detected)
- Ollama >= 0.6.2 (local LLM inference with TurboQuant KV cache compression)

**Package Managers:**
- pip with hatchling build backend (`pyproject.toml`)
- pnpm (frontend, based on `node_modules/.pnpm` structure in `hermes/web/frontend/`)
- Lockfile: `requirements.txt` present for pip; pnpm lockfile in frontend

## Frameworks

**Core Backend:**
- FastAPI >= 0.110 - Dashboard API and web server (`hermes/web/app.py`)
- Uvicorn >= 0.30 - ASGI server for FastAPI
- Pydantic >= 2.0 - Data validation and settings
- Jinja2 >= 3.1 - Server-side HTML templates (`hermes/web/app.py`)

**Frontend:**
- Next.js 16.2.0 - React framework for War Room (`hermes/web/frontend/package.json`)
- React 19.2.4 - UI library
- Tailwind CSS 4.2.0 - Utility-first CSS
- Radix UI - Headless component primitives (full suite: dialog, dropdown, toast, etc.)
- shadcn/ui pattern - Components built on Radix + Tailwind (class-variance-authority, tailwind-merge, clsx)

**Testing:**
- pytest >= 8 - Test runner (`pyproject.toml`)
- pytest-asyncio >= 0.24 - Async test support (mode: `auto`)
- pytest-cov >= 5 - Coverage reporting
- respx >= 0.22 - httpx request mocking

**Linting/Quality:**
- ruff >= 0.4 - Linter and formatter (target: py311, line-length: 100, rules: E, F, I, B, UP)
- mypy >= 1.11 - Type checking (packages: shared, titan, hermes, clawdbot, tools, conway, perseus)
- pre-commit >= 3.0 - Git hooks

**Build/Dev:**
- hatchling - Python build backend (`pyproject.toml`)
- maturin >= 1.12.6 - Rust/Python hybrid builds (dev dependency, likely for Rust extensions)
- Make - Infrastructure operations (`Makefile`)
- just - Agentic/Claude Code operations (`justfile`)

## Key Dependencies

**Critical (revenue path):**
- `httpx >= 0.27` - Primary HTTP client for all API calls (Claude, Ollama, Stripe, Wise, Instantly, etc.)
- `psycopg[binary] >= 3.1` + `psycopg-pool >= 3.2` - Async Postgres with connection pooling (`shared/db.py`)
- `python-telegram-bot >= 22.6` - Telegram bot for operator alerts (`hermes/telegram_bot.py`)
- `openai >= 1.30` - OpenAI-compatible client (used for Ollama's OpenAI-compatible endpoint)

**Frontend Critical:**
- `swr >= 2.2.5` - Data fetching/caching for War Room dashboard
- `recharts 2.15.0` - Revenue and metrics charts
- `@tanstack/react-table >= 8.21.3` - Data tables for leads/pipeline
- `@vercel/analytics 1.6.1` - Analytics integration
- `framer-motion >= 11.15.0` + `gsap >= 3.14.2` - Animations
- `three >= 0.183.2` + `@react-three/fiber >= 9.5.0` + `@react-three/drei >= 10.7.7` - 3D visuals
- `zod >= 3.24.1` - Schema validation
- `react-hook-form >= 7.54.1` - Form handling

**Infrastructure:**
- `python-dotenv >= 1.0.0` - Environment configuration (`shared/config.py`)
- `rich >= 13` - Terminal output formatting
- `click >= 8` - CLI framework (entry point: `jarvis = openjarvis.cli:main`)
- `prometheus-client >= 0.24.1` - Metrics export (`shared/observability.py`)
- `sentry-sdk >= 2.55.0` - Error tracking (`shared/observability.py`)

**Optional Extensions (via extras):**
- `inference-mlx`: MLX-LM for Apple Silicon local inference
- `inference-vllm`: vLLM for GPU inference
- `inference-cloud`: Anthropic + OpenAI SDKs
- `inference-google`: Google GenAI SDK
- `inference-litellm`: LiteLLM unified client
- `memory-graph`: Neo4j driver for graph memory
- `memory-faiss`: FAISS + sentence-transformers for vector search
- `memory-colbert`: ColBERT + PyTorch for retrieval
- `browser`: Playwright for browser automation
- `scheduler`: croniter for cron expressions
- `security-signing`: cryptography for agent key management
- `sandbox-wasm`: wasmtime for WASM sandboxing
- `sandbox-docker`: Docker SDK for container sandboxing
- `speech`: faster-whisper for speech-to-text
- `orchestrator-training`: PyTorch + Transformers for GRPO/LoRA training
- `learning-dspy`: DSPy for prompt optimization
- `eval-wandb`: Weights & Biases for eval tracking
- `docs`: MkDocs + Material theme

## Configuration

**Environment:**
- All config loaded from `.env` via `python-dotenv` in `shared/config.py`
- Typed dataclass config objects: `PostgresConfig`, `OllamaConfig`, `ClaudeConfig`, `TelegramConfig`, `InstantlyConfig`, `FirecrawlConfig`, `PaymentConfig`, `HostingConfig`, `MemoryConfig`, `BudgetConfig`, `PricingConfig`, `ConwayConfig`, `ObservabilityConfig`, `SiteBuildConfig`
- Runtime config overrides stored in Postgres `system_config` table (queried via `shared/db.get_config()`)
- `.env` file contains live API keys -- NEVER read or commit

**Key env var categories:**
- `POSTGRES_*` - Database connection
- `OLLAMA_*` - Local LLM (host, model, kv_cache_type, flash_attention)
- `ANTHROPIC_API_KEY`, `CLAUDE_*_MODEL` - Claude API (primary: claude-sonnet-4-6, fast: claude-haiku-4-5, genius: claude-opus-4)
- `TELEGRAM_*` - Bot token and chat ID
- `INSTANTLY_API_KEY` - Email campaign automation
- `FIRECRAWL_API_KEY` - Web scraping
- `STRIPE_API_KEY`, `WISE_API_TOKEN`, `WISE_PROFILE_ID` - Payments
- `NETLIFY_AUTH_TOKEN` - Site deployment
- `RECRAFT_API_KEY` - AI image generation
- `CONWAY_*` - Agent economics (Base L2, USDC, x402)
- `SENTRY_DSN` - Error tracking
- `MONTHLY_BUDGET_CAP` - Budget enforcement (default: $800)

**Build:**
- `pyproject.toml` - Python project config, build, lint, test settings
- `hermes/web/frontend/next.config.mjs` - Next.js config (unoptimized images, LAN dev access)
- `hermes/web/frontend/tsconfig.json` - TypeScript strict mode, path alias `@/*`

## Infrastructure (Docker Compose)

**Services (`docker-compose.yaml`):**
- Postgres 16.2 (Alpine) - Primary database, port 5432, schema auto-loaded from `scripts/init-db.sql`
- Qdrant v1.8.4 - Vector database for memory/embeddings, port 6333
- Mem0 (custom build from `tools/Dockerfile.mem0_compat`) - Semantic memory layer, port 8888, backed by Qdrant + Ollama embeddings
- Neo4j 5 Community - Graph database for knowledge graphs, ports 7474/7687, APOC plugin
- N8N 1.30.1 - Workflow automation, port 5678
- Prometheus v2.54.1 - Metrics collection, port 9090, scrapes daemon metrics endpoints
- Grafana OSS 11.2.0 - Dashboards, port 3001, provisioned from `ops/grafana/`

**Deployment Docker (`deploy/docker/docker-compose.yml`):**
- Separate compose for deployment with Ollama container
- Dockerfiles: `deploy/docker/Dockerfile`, `Dockerfile.gpu`, `Dockerfile.gpu.rocm`, `Dockerfile.sandbox`

**Network:** All services on `perseus-net` bridge network
**Memory limits:** Postgres 384MB, Qdrant 512MB, Mem0 512MB, Neo4j 512MB, N8N 512MB, Prometheus 256MB, Grafana 256MB

## LLM Stack

**Cloud (Claude API via Anthropic):**
- Genius tier: `claude-opus-4` - Orchestration, complex reasoning
- Smart tier: `claude-sonnet-4-6` - Proposals, strategy, quality work
- Fast tier: `claude-haiku-4-5-20251001` - Primary workhorse
- Budget-aware: auto-downgrades to Ollama at 80% budget threshold
- Claude Max subscription ($200/mo) as primary brain

**Local (Ollama):**
- Primary: `qwen2.5:14b-instruct-q4_K_M` - General tasks
- Secondary: `llama3.2:3b` - Simple classification
- Embeddings: `nomic-embed-text` - Vector embeddings for Mem0/Qdrant
- TurboQuant KV cache compression (turbo4 default: 3.8x memory reduction)
- Flash attention enabled by default

**LLM Client:** Custom unified client (`shared/llm_client.py`) using raw `httpx` for both Claude API and Ollama HTTP endpoints (no Anthropic SDK import)

## Platform Requirements

**Development:**
- macOS (Apple Silicon M4 32GB primary target)
- Docker Desktop for infrastructure services
- Ollama for local LLM inference
- Python 3.11+, Node.js for frontend
- pnpm for frontend package management

**Production:**
- Docker Compose orchestration
- macOS LaunchAgents for daemon management (`scripts/install-launchagents.sh`)
- Postgres for state, Qdrant for vectors, Neo4j for graphs
- Budget: $800/month (Claude API, hosting, external services)

## Monitoring Stack

**Prometheus** (`ops/prometheus/prometheus.yml`):
- Scrapes dashboard at `host.docker.internal:8500`
- Scrapes daemon metrics at ports 9100-9103
- 15-second scrape interval

**Grafana** (`ops/grafana/`):
- Pre-provisioned datasource (Prometheus)
- Dashboard: `ops/grafana/dashboards/objective-hertz-overview.json`

**Sentry** (optional):
- SDK integrated in `shared/observability.py`
- Configurable traces and profiles sample rates
- Graceful no-op when SDK not installed

**Prometheus Client** (in-process):
- Counters, Gauges, Histograms exposed per daemon
- No-op stubs when prometheus_client not installed

---

*Stack analysis: 2026-03-27*
