# External Integrations

**Analysis Date:** 2026-03-27

## APIs & External Services

**LLM / AI:**
- Anthropic Claude API - Primary LLM for email generation, proposals, strategy, orchestration
  - Client: Custom httpx client in `shared/llm_client.py` (no SDK, raw HTTP to Messages API)
  - Auth: `ANTHROPIC_API_KEY`
  - Models: claude-opus-4 (genius), claude-sonnet-4-6 (smart), claude-haiku-4-5 (fast)
  - Budget-gated: auto-downgrades to Ollama at 80% monthly cap

- Ollama - Local LLM inference (fallback and cost-free tasks)
  - Client: Custom httpx client in `shared/llm_client.py` (HTTP to `/api/generate` and `/api/chat`)
  - Endpoint: `OLLAMA_HOST` (default: `http://localhost:11434`)
  - Models: qwen2.5:14b (primary), llama3.2:3b (secondary), nomic-embed-text (embeddings)
  - TurboQuant KV cache compression via `OLLAMA_KV_CACHE_TYPE`

- Recraft AI - Image and vector generation for client websites
  - Client: `tools/recraft_client.py`
  - Auth: `RECRAFT_API_KEY`
  - API: `https://external.api.recraft.ai/v1`
  - Cost: ~$0.01/image, ~$1/month at projected volume

**Email / Outreach:**
- Instantly.ai - Cold email campaign automation
  - Client: `tools/instantly_client.py` (class `InstantlyClient`)
  - Auth: `INSTANTLY_API_KEY` (Bearer token)
  - API: `https://api.instantly.ai/api/v2`
  - Rate limited: 0.2s minimum between requests, 3 retries
  - Flow: Create campaign -> Add leads with variables -> Instantly handles delivery

**Web Scraping:**
- Firecrawl - Web page scraping and content extraction
  - Client: `tools/firecrawl_client.py`
  - Auth: `FIRECRAWL_API_KEY`
  - API: `https://api.firecrawl.dev/v1` (hosted) or self-hosted
  - Includes bundled self-hosted Firecrawl source in `tools/firecrawl/`

**Payments:**
- Stripe - Primary payment processing
  - Client: `tools/payment_router.py` (class `PaymentRouter`)
  - Auth: `STRIPE_API_KEY`
  - Creates invoices and payment links

- Wise (TransferWise) - Fallback payment processing (Mexican bank accounts)
  - Client: `tools/payment_router.py` (class `PaymentRouter`)
  - Auth: `WISE_API_TOKEN` + `WISE_PROFILE_ID`
  - Used when Stripe unavailable

**Site Deployment:**
- Netlify - Static site deployment for client websites
  - Client: `clawdbot/netlify_deploy.py`
  - Auth: `NETLIFY_AUTH_TOKEN`
  - API: `https://api.netlify.com/api/v1`
  - File-digest deploy for multi-page static sites

**Communication:**
- Telegram Bot API - Operator alerts, commands, morning briefings
  - Client: `hermes/telegram_bot.py` (uses `python-telegram-bot` SDK)
  - Auth: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
  - Chat-locked to operator's configured chat ID

**Blockchain / Crypto:**
- Base L2 (Ethereum) - Agent wallets with USDC
  - Client: `conway/wallet.py` (class `AgentWallet`)
  - RPC: `BASE_RPC_URL` (default: `https://mainnet.base.org`)
  - Contract: USDC on Base (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
  - Raw ERC-20 calls via httpx (balanceOf, transfer)

- x402 Protocol - Micropayments between agents
  - Client: `conway/x402_client.py`
  - Facilitator: `X402_FACILITATOR_URL` (default: `https://x402.org/facilitator`)
  - Conway API: `CONWAY_API_URL` (default: `https://api.conway.tech`)

**Analytics:**
- Vercel Analytics - Frontend analytics for War Room
  - SDK: `@vercel/analytics` in `hermes/web/frontend/package.json`

## Data Storage

**Databases:**
- PostgreSQL 16.2 (Alpine) - Primary relational database
  - Connection: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`
  - Client: psycopg v3 async with connection pool (`shared/db.py`, class `AsyncConnectionPool`)
  - DSN format: `postgresql://{user}:{password}@{host}:{port}/{db}`
  - Schema: 23+ tables (`scripts/init-db.sql`) including clients, deals, hosting_subscriptions, receptionist_subscriptions, outreach_metrics, task_queue, events, system_config, conway_wallets, etc.
  - Pool: min 2, max 10 connections, dict_row factory

- Qdrant v1.8.4 - Vector database for semantic search and memory
  - Connection: `QDRANT_HOST` (default: `http://localhost:6333`)
  - Collection: `QDRANT_COLLECTION` (default: `perseus`)
  - Used by Mem0 for embedding storage

- Neo4j 5 Community - Graph database for knowledge graphs
  - Connection: `NEO4J_URI` (default: `bolt://localhost:7687`)
  - Auth: `NEO4J_USER` / `NEO4J_PASSWORD`
  - Optional: `memory-graph` extra

- Mem0 - Semantic memory layer (custom Docker build)
  - Connection: `MEM0_HOST` (default: `http://localhost:8888`)
  - Backed by Qdrant (vectors) + Ollama (embeddings)
  - Embedding dims: 768 (nomic-embed-text)

**File Storage:**
- Local filesystem only
- Conway keystores: `conway/data/keystores/` (encrypted wallet files)
- Logs: `logs/` directory (perseus.log, titan.log, clawdbot.log, dashboard.log)
- Templates: `templates/` (industry website templates)

**Caching:**
- None (no Redis or dedicated cache layer)
- N8N has its own internal state

## Authentication & Identity

**Dashboard Auth:**
- Custom session-based auth (`hermes/web/app.py`)
- `DASHBOARD_SECRET` env var required (fail-closed without it)
- HMAC-signed HttpOnly session cookie (`perseus_session`)
- Also supports `Authorization: Bearer <secret>` header
- Cookie signing key regenerated per process (restart invalidates sessions)

**Telegram Auth:**
- Chat ID whitelist (`TELEGRAM_CHAT_ID`) - single operator
- All commands checked via `_require_chat_access()`

**Agent Auth (Conway):**
- Encrypted keystores on disk for Ethereum wallets
- Private keys validated on init (66-char hex string)

## Monitoring & Observability

**Error Tracking:**
- Sentry SDK (optional, graceful no-op)
  - Config: `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILES_SAMPLE_RATE`
  - Integration: `shared/observability.py`

**Metrics:**
- Prometheus client (in-process)
  - Counters, Gauges, Histograms per daemon
  - Exposed at `/metrics` endpoint on each daemon
  - Dashboard metrics at port 8500
  - Daemon metrics at ports 9100-9103

**Dashboards:**
- Grafana OSS 11.2.0 at port 3001
  - Pre-provisioned Prometheus datasource (`ops/grafana/provisioning/datasources/prometheus.yml`)
  - Overview dashboard (`ops/grafana/dashboards/objective-hertz-overview.json`)

**Logs:**
- Python `logging` module throughout
- Logger naming: `perseus.*` (e.g., `perseus.db`, `perseus.llm`, `perseus.tools.payment`)
- Log files in `logs/` directory
- Health checks every 30 seconds

## CI/CD & Deployment

**Hosting:**
- Local Mac M4 32GB (primary development and production)
- Docker Compose for infrastructure services
- macOS LaunchAgents for daemon persistence (`scripts/install-launchagents.sh`)

**CI Pipeline:**
- No dedicated CI/CD service detected
- Quality gates run locally: `make quality` (ruff + mypy + pytest)
- pre-commit hooks configured

**Deployment Docker:**
- `deploy/docker/Dockerfile` - Standard deployment
- `deploy/docker/Dockerfile.gpu` - GPU deployment (NVIDIA)
- `deploy/docker/Dockerfile.gpu.rocm` - GPU deployment (AMD ROCm)
- `deploy/docker/Dockerfile.sandbox` - Sandboxed execution
- `deploy/docker/docker-compose.yml` - Deployment compose with Ollama

**Build Commands:**
```bash
make start          # Start all daemons + dashboard + frontend
make stop           # Stop all daemons
make up             # Start Docker services (Postgres, Qdrant, Mem0, N8N)
make down           # Stop Docker services
make status         # Check daemon and service health
make health         # Quick health check (all services)
make quality        # Run lint + typecheck + test
make test           # Run pytest
make lint           # Run ruff
make typecheck      # Run mypy
make backup         # Postgres backup
make restore        # Postgres restore
make dashboard      # Start dashboard standalone on port 8500
```

## Workflow Automation

**N8N 1.30.1:**
- Port 5678
- Used for workflow automation
- Basic auth protected
- Webhook URL configurable

## Agent Communication

**A2A Protocol (Google spec):**
- Implementation: `openjarvis/a2a/` (client, server, protocol, tool)
- HTTP JSON-RPC between agents
- Feature flagged: `USE_A2A_DISPATCH` env var (default: enabled)

**Postgres Task Queue:**
- Fallback communication via `task_queue` table
- Events table for broadcast status/results
- `shared/comms.py` provides unified API

**Shared Memory:**
- `titan_learnings` table - Structured memory
- Mem0 vector store - Semantic memory (searchable by any daemon)
- `system_config` table - Runtime configuration

## Environment Configuration

**Required env vars (minimum viable):**
- `POSTGRES_PASSWORD` - Database access
- `ANTHROPIC_API_KEY` - Claude API (or system runs Ollama-only)
- `DASHBOARD_SECRET` - Dashboard authentication
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` - Operator alerts

**Required for revenue pipeline:**
- `INSTANTLY_API_KEY` - Email campaigns
- `STRIPE_API_KEY` or `WISE_API_TOKEN` + `WISE_PROFILE_ID` - Payment collection
- `NETLIFY_AUTH_TOKEN` - Site deployment
- `RECRAFT_API_KEY` - Image generation

**Optional:**
- `FIRECRAWL_API_KEY` - Web scraping
- `CONWAY_*` - Agent economics (blockchain wallets)
- `SENTRY_DSN` - Error tracking
- `NEO4J_*` - Graph memory

**Secrets location:**
- `.env` file at project root (NEVER read or commit)
- Conway keystores at `conway/data/keystores/` (encrypted)

**Runtime honesty pattern:**
- `tools/runtime_honesty.py` provides `env_is_configured()` and `truth_payload()` helpers
- Every external tool client checks if its API key is configured before attempting calls
- Returns structured status payloads indicating "live", "blocked", or "degraded"

## Webhooks & Callbacks

**Incoming:**
- N8N webhook endpoint at port 5678 (`WEBHOOK_URL`)
- Hermes A2A server (`hermes/a2a_server.py`)
- ClawdBot A2A server (`clawdbot/a2a_server.py`)

**Outgoing:**
- Telegram bot API (alerts, briefings)
- Stripe/Wise payment webhooks (if configured)

---

*Integration audit: 2026-03-27*
