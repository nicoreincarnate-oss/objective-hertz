# Integration Points

**Analysis Date:** 2026-03-29

## External APIs (with auth patterns)

### LLM / AI

**Anthropic Claude API:**
- Client: `shared/llm_client.py` (class `LLMClient`, singleton `llm`)
- Transport: Raw httpx POST to `https://api.anthropic.com/v1/messages`
- Auth: `x-api-key` header from `config.claude.api_key` (`ANTHROPIC_API_KEY`)
- API version: `anthropic-version: 2023-06-01`
- Models resolved via `_resolve_model()` which checks DB override first, then `.env`:
  - `genius` -> `claude-opus-4` (orchestration)
  - `smart`/`primary` -> `claude-sonnet-4-6` (proposals, strategy)
  - `fast` -> `claude-haiku-4-5-20251001` (workhorse)
- Vision support: `generate_with_images()` sends base64-encoded PNG via content blocks
- No Anthropic SDK import — pure httpx

**Ollama (Local LLM):**
- Client: `shared/llm_client.py` (same `LLMClient` class)
- Transport: httpx POST to `{config.ollama.host}/api/generate` and `/api/embeddings`
- Auth: None (local service)
- Models: `qwen2.5:14b-instruct-q4_K_M` (primary), `llama3.2:3b` (secondary), `nomic-embed-text` (embeddings)
- TurboQuant KV cache: `cache_type_k`/`cache_type_v` options sent in request body
- Flash attention: boolean option in request body
- Fine-tuned model override: checks DB `fine_tuned_model` key per pipeline stage

**Recraft AI (Image Generation):**
- Client: `tools/recraft_client.py`
- Transport: httpx POST to `https://external.api.recraft.ai/v1/images/generations`
- Auth: `Authorization: Bearer {RECRAFT_API_KEY}`
- Functions: `generate_image()`, `generate_logo()`, `generate_hero_image()`, `generate_social_graphic()`, `remove_background()`
- Cost: ~$0.01/image

### Email / Outreach

**Instantly.ai (Cold Email Automation):**
- Client: `tools/instantly_client.py` (class `InstantlyClient`)
- Transport: httpx to `https://api.instantly.ai/api/v2`
- Auth: `Authorization: Bearer {INSTANTLY_API_KEY}`
- Rate limiting: 0.2s min between requests, process-wide asyncio lock, 3 retries with Retry-After support
- Key operations: campaigns (CRUD, activate/stop), leads (add/bulk), emails (list/reply), warmup, email verification, DFY accounts
- Perseus owns sequencing — Instantly's built-in sequences disabled on campaign creation

### Web Scraping

**Firecrawl:**
- Client: `tools/firecrawl_client.py`
- Transport: requests (sync!) POST to `https://api.firecrawl.dev/v1` or self-hosted
- Auth: `Authorization: Bearer {FIRECRAWL_API_KEY}`
- Functions: `scrape_url()`, `search_web()`, `enrich_business_profile()`
- Supports dual mode: hosted API vs self-host (`FIRECRAWL_SELF_HOST_URL`)
- Bundled self-hosted source: `tools/firecrawl/`

### Payments

**Stripe (Primary):**
- Client: `tools/payment_router.py` (class `PaymentRouter`)
- Transport: httpx POST to `https://api.stripe.com/v1/payment_links` and GET `/v1/payment_intents`
- Auth: `Authorization: Bearer {STRIPE_API_KEY}` + `Idempotency-Key` header
- Creates payment links with metadata propagation to PaymentIntents for reconciliation
- Idempotent retry on failure before considering Wise fallback
- Fail-closed: refuses Wise fallback if Stripe status is ambiguous

**Wise (Check-Only):**
- Client: `tools/payment_router.py` (same class)
- Transport: httpx to `https://api.wise.com/v1/transfers` and `/v3/profiles/{id}/quotes`
- Auth: `Authorization: Bearer {WISE_API_TOKEN}`
- Invoice creation DISABLED (raises RuntimeError — Wise creates outbound transfers, not payment requests)
- Retained for payment reconciliation polling only

**Conway x402 (Crypto):**
- Client: `conway/wallet.py`, `conway/x402_client.py`, `conway/ledger.py`
- Base L2 USDC wallet transactions
- Gated: `CONWAY_ENABLED` must be true

### Site Deployment

**Netlify:**
- Client: `clawdbot/netlify_deploy.py`
- Auth: `NETLIFY_AUTH_TOKEN`
- API: `https://api.netlify.com/api/v1`
- File-digest deploy for static sites

**Cloudflare DNS:**
- Client: `tools/domain_manager.py`
- Transport: httpx to `https://api.cloudflare.com/client/v4`
- Auth: `Authorization: Bearer {CLOUDFLARE_API_TOKEN}`
- Zone lookup, DNS record CRUD for custom domains

### Communication

**Telegram Bot API:**
- Client: `hermes/telegram_bot.py` (uses `python-telegram-bot` SDK)
- Auth: `TELEGRAM_BOT_TOKEN`
- Chat-locked to `TELEGRAM_CHAT_ID`

### Workflow Automation

**N8N:**
- Client: `tools/n8n_client.py`
- Transport: httpx to `http://localhost:5678`
- Auth: Basic auth (`N8N_USER`/`N8N_PASSWORD`) for API; webhooks are unauthenticated
- Functions: `trigger_workflow()`, `list_workflows()`, `get_n8n_status()`

### Experimental

**Google NotebookLM:**
- Client: `tools/notebooklm_client.py`
- Auth: Google account cookies (unofficial `notebooklm-py` package)
- Used for: audio briefings, infographics, research synthesis

## Internal Service Communication (A2A, DB, EventBus)

### A2A Protocol (Primary)

**Implementation:** `openjarvis/a2a/` (client, server, protocol, tool)
**Bridge:** `shared/oj_bridge.py` — singleton factory for all OJ primitives

**Agent Endpoints:**
```
orchestrator  -> http://localhost:9000  (ORCHESTRATOR_A2A_URL)
titan         -> http://localhost:9001  (TITAN_A2A_URL)
hermes        -> http://localhost:9002  (HERMES_A2A_URL)
clawdbot      -> http://localhost:9003  (CLAWDBOT_A2A_URL)
ruflo         -> http://localhost:9004  (RUFLO_A2A_URL)
```

**Call Pattern:**
```python
# shared/oj_bridge.py — primary inter-agent call
from shared.oj_bridge import call_agent_async
result = await call_agent_async("titan", "lead_discovery", {"industry": "dental"}, timeout=120.0)
```

**Higher-level wrappers in `shared/comms.py`:**
- `request_task()` — Route via capability router or TASK_ROUTING, fallback to DB
- `request_task_result()` — Synchronous call-response with timeout
- `delegate_task()` — Direct to named agent, bypasses TASK_ROUTING
- `ask_agent()` — Question-answer between agents
- `escalate_to_boss()` — Escalate to orchestrator + alert

**Feature flag:** `USE_A2A_DISPATCH=0` disables A2A, forces DB-only communication.

**Trace context:** Every A2A call enriched with `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id` headers via `shared/observability.ensure_trace_context()`.

### Postgres Task Queue (Fallback)

**Table:** `task_queue` (status: pending/running/completed/failed/dead_letter)
**Pattern:** Insert task -> poll for completion -> emit event on result
**Used when:** A2A call fails or is disabled

### EventBus (OpenJarvis)

**Access:** `shared/oj_bridge.get_bus()` — returns OpenJarvis `EventBus` singleton
**Used by:** TraceStore (subscribes to all events), broadcast alerts, decision recording
**Pattern:** Pub/sub within a single process; cross-process uses A2A or DB events table

### Postgres Events Table

**Table:** `events` (event_type, payload JSONB, acknowledged, created_at)
**Pattern:** `shared/db.emit_event()` writes, `shared/comms.wait_for_event()` polls
**Used for:** Cross-daemon broadcast, Hermes alert pickup, decision/recommendation visibility

### Shared Memory Channels

| Channel | Storage | Access Pattern | Used By |
|---------|---------|---------------|---------|
| Structured learnings | `titan_learnings` table | `shared/comms.store_learning()` / `get_learnings()` | All agents (write), Titan (primary reader) |
| Vector memory | Mem0 -> Qdrant | `shared/comms.store_vector_memory()` / `search_vector_memory()` | All agents |
| Agent decisions | `agent_decisions` table | `shared/comms.record_decision()` / `get_recent_decisions()` | All agents (audit trail) |
| Runtime config | `system_config` table | `shared/db.get_config()` / `set_config()` | All agents, dashboard |
| Agent status | `agent_registry` table | `shared/comms.is_agent_alive()` | Health checks |

## LLM Client Architecture (Model Routing, Budget Gating, System Prompt Injection)

### Entry Point: `shared/llm_client.py`

**Singleton:** `llm = LLMClient()` — import via `from shared.llm_client import llm`

### Model Routing

```python
# Tier-based model selection with DB override
async def _resolve_model(tier: str) -> str:
    _MODEL_MAP = {
        "genius": ("model_genius", config.claude.genius_model),    # claude-opus-4
        "fast":   ("model_fast", config.claude.fast_model),        # claude-haiku-4-5
        "smart":  ("model_primary", config.claude.primary_model),  # claude-sonnet-4-6
        "local":  ("model_local", config.ollama.model),            # qwen2.5:14b
        "local-small": ("model_local_small", config.ollama.secondary),  # llama3.2:3b
        "embed":  ("model_embed", config.ollama.embed_model),      # nomic-embed-text
    }
    # Check system_config DB table first, then fall back to .env
    override = await get_config(db_key, None)
    return str(override) if override else fallback
```

### Budget Gating

```python
# _budget_gate() in shared/llm_client.py (lines 179-206)
# 1. Query: SELECT SUM(amount) FROM v_effective_budget_tracking WHERE month = current_month
# 2. If >= 100% of monthly_cap ($800): ALL Claude -> Ollama
# 3. If >= 80% (alert_threshold): "fast" (Haiku) -> Ollama, "smart" (Sonnet) stays on Claude
# 4. If budget check fails: ALLOW the call (fail open)
```

### System Prompt Injection Point (Agent DNA)

**This is the critical injection point for Agent DNA integration.**

```python
# shared/llm_client.py line 67-77
async def generate(
    self,
    prompt: str,
    *,
    system: str = "",          # <-- AGENT DNA INJECTS HERE
    model: str = "auto",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    client_id: int | None = None,
    pipeline_stage: str = "",
) -> str:
```

**How callers use it today:**
```python
# Example from skill_loader.py — loads SKILL.md as system prompt
result = await llm.generate(prompt, system=skill_content, model="fast")

# Example from titan pipeline — stage-specific prompts
result = await llm.generate(user_prompt, system=stage_system_prompt, model="smart")
```

**DNA injection strategy:** Prepend/append DNA directives to the `system` parameter before it reaches `_claude_generate()` or `_ollama_generate()`. Both methods pass `system` directly to the API body:
- Claude: `body["system"] = system` (line 262)
- Ollama: `body["system"] = system` (line 377)

### Spend Recording

Every Claude call records cost to `budget_tracking` table:
- Token estimation: ~4 chars/token (rough)
- Cost lookup: haiku=$0.001/1K, sonnet=$0.006/1K, opus=$0.045/1K
- Tagged with `client_id` and `pipeline_stage` for attribution

### Fine-Tuned Model Support

`_resolve_ollama_model()` (line 339) checks DB for `fine_tuned_model` key when a `pipeline_stage` is provided. Allows hot-swapping local models per pipeline stage without restart.

## Vector Store Usage (Mem0, Qdrant)

### Architecture

```
Callers (any daemon)
    -> shared/comms.py (store_vector_memory / search_vector_memory)
        -> titan/memory.py (store_memory / search_memory)
            -> HTTP POST to Mem0 service (port 8888)
                -> Mem0 container (tools/mem0_compat_service.py)
                    -> Qdrant (port 6333, collection: "perseus")
                    -> Ollama nomic-embed-text (embeddings)
```

### Write Pattern (from `titan/memory.py` lines 34-78)

```python
async def store_memory(content, category, client_id=None, metadata=None,
                       outcome_magnitude=0.5, sample_size=1):
    importance = _compute_importance(outcome_magnitude, sample_size)
    mem0_user_id = f"client:{client_id}" if client_id else "titan"
    await http.post(f"{config.memory.mem0_host}/v1/memories/", json={
        "messages": [{"role": "assistant", "content": content}],
        "user_id": mem0_user_id,       # Namespace isolation per client
        "metadata": {
            "category": category,       # discovery, email, sales, pricing, etc.
            "importance": importance,    # Statistical significance score
            "sample_size": sample_size,
        },
    })
```

### Read Pattern

```python
# titan/memory.py — search by similarity
async def search_memory(query, limit=5):
    resp = await http.post(f"{config.memory.mem0_host}/v1/memories/search/", json={
        "query": query,
        "user_id": mem0_user_id,
        "limit": limit,
    })
```

### Mem0 Compat Service (`tools/mem0_compat_service.py`)

Lightweight FastAPI service that wraps `mem0ai` SDK (version 1.0.6). Provides:
- `POST /v1/memories/` — Store
- `POST /v1/memories/search/` — Search
- `DELETE /v1/memories/{id}/` — Delete
- `GET /health` — Health check

Backed by SQLite history db + Qdrant for vectors. Uses Ollama via `OPENAI_BASE_URL` env var (pointed at Ollama's OpenAI-compat endpoint).

### Embedding Configuration

- Model: `nomic-embed-text` via Ollama
- Dimensions: 768 (`MEM0_EMBEDDING_DIMS=768` in docker-compose)
- Direct embedding: `llm.embed(text)` calls Ollama `/api/embeddings` endpoint

### MAGMA (Graph-Augmented Memory — `shared/magma.py`)

Four graph types stored in Neo4j:
1. **TEMPORAL** — Immutable chain of events
2. **CAUSED** — LLM-inferred causal links with confidence
3. **SIMILAR_TO** — Semantic edges from embedding cosine similarity (threshold: 0.75)
4. **INVOLVES** — Entity deduplication

Features:
- RRF (Reciprocal Rank Fusion) merges results across Qdrant + Neo4j + Zep
- Beam search: `lambda1 * structural + lambda2 * cosine_similarity`
- Memory evolution: decay (14-day half-life), strengthen, contradict, semantic merge (threshold: 0.92)
- Confidence abstention: memories below 0.4 confidence are filtered
- Domain segregation: only loads memories matching current task domain
- Gated: `MAGMA_ENABLED=1` (default: disabled)

## Docker Services (What's Containerized, What's Not)

### Containerized (via `docker-compose.yaml`)

| Service | Containerized | Network | Health Check |
|---------|:---:|---------|-------------|
| Postgres 16.2 | Yes | perseus-net | `pg_isready` |
| Qdrant v1.8.4 | Yes | perseus-net | TCP port check |
| Mem0 (custom) | Yes | perseus-net | HTTP /health |
| Neo4j 5 | Yes | perseus-net | HTTP /7474 |
| N8N 1.30.1 | Yes | perseus-net | HTTP /healthz |
| Prometheus | Yes | perseus-net | None |
| Grafana | Yes | perseus-net | None |

### NOT Containerized (run as native processes)

| Component | Runs As | Why |
|-----------|---------|-----|
| Perseus daemon | Python process (LaunchAgent) | Direct filesystem/hardware access |
| Titan daemon | Python process (LaunchAgent) | Same |
| Hermes daemon + dashboard | Python process (LaunchAgent) | Same |
| ClawdBot daemon | Python process (LaunchAgent) | Browser automation needs host |
| Ollama | Native binary | Metal GPU access for Apple Silicon |
| OpenJarvis orchestrator | Python process | Top-level entry point |
| War Room frontend | Node.js dev server | Development convenience |

### Sandbox Containers Available

- `deploy/docker/Dockerfile.sandbox` — Full Python 3.12 + Node.js 22 sandbox with all daemon code
- `deploy/docker/Dockerfile` — Production multi-stage build
- `deploy/docker/Dockerfile.gpu` / `Dockerfile.gpu.rocm` — GPU inference containers

## Intel Integration Mapping

### Agent DNA (`intel/agent-dna/`)
**Touches:** `shared/llm_client.py` — the `system` parameter in `LLMClient.generate()`
**Integration approach:** Create a DNA resolver that produces a system prompt string. Prepend it to any existing `system` parameter in `generate()`. No new dependencies required.
**Key files:**
- `shared/llm_client.py` lines 67-124 — `generate()` method
- `shared/skill_loader.py` — Already loads SKILL.md as system prompts (precedent)
- `shared/config.py` — Would need new `AgentDNAConfig` dataclass if DNA has config

### Recursive Language Models (`intel/recursive-language-models/`)
**Touches:** `shared/magma.py` (4-graph memory), `titan/memory.py` (Mem0), Qdrant, Neo4j
**Integration approach:** MAGMA already implements multi-graph retrieval with RRF fusion. RLM context would be stored as memories in existing infrastructure. Recursive retrieval patterns fit the beam search traversal in MAGMA.
**Key files:**
- `shared/magma.py` — Full MAGMA implementation with beam search, RRF, confidence scoring
- `titan/memory.py` — Mem0 read/write wrappers
- `shared/comms.py` lines 225-241 — Cross-daemon vector memory access

### HyperAgents (`intel/hyperagents/`)
**Touches:** OpenJarvis agent framework, Docker sandbox
**Integration approach:** Agent isolation via `deploy/docker/Dockerfile.sandbox`. OpenJarvis `AgentManager` (`shared/oj_bridge.get_agent_manager()`) handles lifecycle. `CapabilityPolicy` (`shared/oj_bridge.get_capability_policy()`) enforces RBAC with default-deny.
**Key files:**
- `shared/oj_bridge.py` lines 104-139 — AgentManager + CapabilityPolicy with agent grants
- `deploy/docker/Dockerfile.sandbox` — Sandbox container
- `openjarvis/agents/` — Agent base classes
**Gap:** `docker >= 7.0` SDK not installed by default; `sandbox-docker` is an optional extra

### Post-Quantum Crypto (`intel/post-quantum-crypto/`)
**Touches:** `rust/crates/openjarvis-security/` (ed25519-dalek), `conway/wallet.py`
**Integration approach:** Would need to add `liboqs-python` or `pqcrypto` to dependencies, then wrap PQ key exchange in the existing signing infrastructure. Rust side uses `ed25519-dalek` which could be supplemented with a PQ crate.
**Key files:**
- `rust/Cargo.toml` — `ed25519-dalek = { version = "2", features = ["rand_core"] }`
- `conway/wallet.py` — Ethereum wallet signing
- `shared/oj_bridge.py` line 142 — AuditLogger with Merkle chain
**Gap:** **No post-quantum crypto library exists anywhere in the dependency tree.** This is a ground-up addition.

### DeerFlow (`intel/deerflow/`)
**Touches:** `shared/pipeline_dag.py`, OpenJarvis WorkflowEngine
**Integration approach:** WorkflowEngine already supports DAG-based execution with max_parallel=4. DeerFlow patterns (research planning -> execution -> synthesis) map directly to existing DAG nodes.
**Key files:**
- `shared/oj_bridge.py` line 153 — `get_workflow_engine()` with `max_parallel=4`
- `shared/pipeline_dag.py` — Pipeline DAG definitions
- `titan/workflow_pipeline.py` — WorkflowEngine-based pipeline

### Anti-Slop (`intel/anti-slop/`)
**Touches:** `shared/llm_client.py` system prompts, quality gates
**Integration approach:** Anti-slop constraints inject as system prompt additions (same mechanism as Agent DNA). Quality scoring can run as a post-generation validation step using `llm.generate()` with a scoring prompt.
**Key files:**
- `shared/llm_client.py` — System prompt injection point
- `shared/skill_loader.py` — Skill-as-system-prompt pattern (precedent for quality directives)
- `tools/runtime_honesty.py` — Existing "truth payload" pattern for honest status reporting

## Runtime Honesty Pattern

All external tool clients use a shared truth payload system from `tools/runtime_honesty.py`:

```python
# Every tool checks availability before calling
status = get_firecrawl_status()  # Returns {"mode": "live"|"blocked"|"degraded"|"needs_input", ...}
if status["mode"] != "live":
    return status  # Caller gets structured error, not an exception
```

This pattern ensures the system never silently pretends an API call succeeded when the service is unavailable.

## Environment Configuration

**Required env vars (minimum viable):**
- `POSTGRES_PASSWORD` — Database
- `ANTHROPIC_API_KEY` — Claude API (falls back to Ollama-only without it)
- `DASHBOARD_SECRET` — Web dashboard auth
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Alerts

**Required for revenue pipeline:**
- `INSTANTLY_API_KEY` — Email campaigns
- `STRIPE_API_KEY` — Payment collection
- `NETLIFY_AUTH_TOKEN` — Site deployment
- `RECRAFT_API_KEY` — Image generation

**Optional:**
- `FIRECRAWL_API_KEY` — Web scraping (or self-host)
- `CLOUDFLARE_API_TOKEN` — Custom domain DNS
- `CONWAY_*` — Agent economics (disabled by default)
- `SENTRY_DSN` — Error tracking
- `NEO4J_*` — Graph memory (for MAGMA)
- `RUFLO_*` — Ruflo engineering agent
- `N8N_USER`/`N8N_PASSWORD` — N8N workflow API access

**Secrets:** `.env` at project root (NEVER read or commit). Conway keystores at `conway/data/keystores/`.

---

*Integration audit: 2026-03-29*
