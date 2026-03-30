# Code Conventions

**Analysis Date:** 2026-03-29

## Naming & Style (Python Conventions)

**Files:**
- Snake_case for all Python files: `lead_discovery.py`, `email_compose.py`, `skill_loader.py`
- Daemon entry points are always `daemon.py` within each agent directory
- Pipeline stages live in `titan/pipeline/` as individual files per stage

**Functions:**
- Snake_case for all functions: `discover_leads()`, `compose_emails()`, `process_follow_ups()`
- Private/internal functions prefixed with underscore: `_compose_one()`, `_budget_gate()`, `_load_soul_copy()`
- Async functions use `async def` consistently across pipeline stages

**Variables:**
- Snake_case throughout: `lead_score`, `batch_size`, `soul_copy`
- Constants are UPPER_SNAKE_CASE: `DISCOVERY_SKILLS`, `_COST_PER_1K`, `SKILL_DIRS`
- Type hints used on function signatures but not consistently on local variables

**Classes:**
- PascalCase: `LLMClient`, `FakeDB`, `PostgresConfig`
- Frozen dataclasses for config: `@dataclass(frozen=True)` pattern in `shared/config.py`

**Loggers:**
- Namespaced under `perseus.*` hierarchy: `logging.getLogger("perseus.titan.discovery")`, `logging.getLogger("perseus.db")`
- Every module creates its own `logger` at module scope

## Error Handling Patterns

**Standard pattern — log + emit pipeline error + continue:**
```python
# titan/pipeline/email_compose.py:78-86
for lead in leads:
    try:
        await _compose_one(lead, soul_copy, learned_tips, rules_block, prompt_hash)
    except Exception as e:
        logger.error(f"Email compose failed for lead {lead['id']}: {e}")
        await emit_pipeline_error("email_compose", e, lead_id=lead["id"])
```
Use `shared/pipeline_alerts.py:emit_pipeline_error()` to surface errors as structured events visible to Hermes/ops.

**Fallback-on-failure pattern (LLM calls):**
```python
# shared/llm_client.py:111-124
try:
    result = await self._claude_generate(prompt, system, model, max_tokens, temperature)
    return result
except Exception as e:
    logger.warning(f"Claude API failed, falling back to Ollama: {e}")
    return await self._ollama_generate(prompt, system, "local", max_tokens, temperature, pipeline_stage)
```

**Budget gate — fail open:**
```python
# shared/llm_client.py:202-206
except Exception as e:
    logger.warning(f"Budget check failed (allowing call): {e}")
```
Non-critical subsystems (budget checks, MAGMA, decision logging) catch broadly and continue.

**DB layer — re-raise with observability:**
```python
# shared/db.py:89-99
except Exception as exc:
    observe_db_query(operation, time.perf_counter() - started_at, success=False)
    capture_exception(exc, service_name="db", category="query", extra_context={"operation": operation})
    raise
```
DB errors are NOT swallowed — they propagate up to the caller.

**Silent swallowing (anti-pattern, documented):**
Found in non-critical paths: `perseus/daemon.py:215` (`pass` after `except Exception:`), `perseus/misalignment_probe.py` (returns safe defaults), `perseus/sleep_cycle.py` (multiple instances). These are annotated as "non-critical" in log messages. New code SHOULD NOT follow this pattern without explicit justification.

## Async Patterns

**All pipeline stages are async:**
Every function in `titan/pipeline/*.py` uses `async def`. The pipeline is fully async-native.

**LLM client is async:**
`shared/llm_client.py` — `LLMClient.generate()` is `async def`. Uses `httpx.AsyncClient` for HTTP.

**DB layer is async:**
`shared/db.py` — `psycopg` v3 async with `AsyncConnectionPool`. All DB functions (`execute`, `fetch_one`, `fetch_all`, `fetch_val`) are `async def`.

**Event loop usage:**
Daemons run their own `asyncio` event loop. Tests that call async code use `asyncio.run(coro)` helper pattern:
```python
def run(coro):
    return asyncio.run(coro)
```
pytest-asyncio is configured with `asyncio_mode = "auto"` in `pyproject.toml`.

**Context manager for transactions:**
```python
# shared/db.py:76-81
@asynccontextmanager
async def transaction():
    async with get_conn() as conn:
        async with conn.transaction():
            yield conn
```

## LLM Call Patterns

**Primary interface:** `shared/llm_client.py` exposes a singleton `llm` (instance of `LLMClient`).

**Call signature:**
```python
await llm.generate(
    prompt="user prompt here",
    system="system prompt here",         # Optional system prompt
    model="fast",                         # "fast"=Haiku, "smart"=Sonnet, "genius"=Opus, "local"=Ollama
    max_tokens=2048,
    temperature=0.7,
    client_id=42,                         # Optional — tags spend to a lead
    pipeline_stage="email_compose",       # Optional — tags for budget tracking
)
```

**Model tier routing:**
- `"fast"` / `"auto"` -> Claude Haiku (primary workhorse)
- `"smart"` / `"primary"` -> Claude Sonnet (proposals, strategy)
- `"genius"` -> Claude Opus (orchestration, complex reasoning)
- `"local"` -> Ollama primary model (free, simple tasks)
- `"local-small"` -> Ollama secondary (classification only)
- Dashboard overrides checked first via `shared/db.get_config()`, then falls back to `shared/config.py` values

**Skill-as-system-prompt pattern (DNA injection):**
Skills are markdown files (`SKILL.md`) loaded as the `system=` parameter:
```python
# shared/skill_loader.py:107-111
result = await llm.generate(
    prompt=task_prompt + ctx,
    system=skill_content,      # SKILL.md content becomes the system prompt
    model=model,
)
```
The `soul/soul_copy.md` file is loaded as inline prompt context (not system prompt) for email composition. Skills directories: `hermes/skills/`, `~/.hermes/skills/`, `~/.openclaw/skills/`, `.agent/skills/`.

**JSON extraction from LLM output:**
```python
# titan/pipeline/email_compose.py:196-198
start = result.find("{")
end = result.rfind("}") + 1
email_data = json.loads(result[start:end])
```
This is the standard pattern — find first `{` to last `}`, parse as JSON. Fragile but consistent across the codebase.

## Database Access Patterns

**Parameterized queries (correct pattern):**
```python
# titan/pipeline/email_compose.py:50-57
leads = await fetch_all(
    """SELECT id, business_name, contact_name, email, industry,
              research_summary, lead_score, language, country, city
       FROM clients WHERE status = 'researched'
         AND email IS NOT NULL AND email != ''
       ORDER BY lead_score DESC, created_at ASC LIMIT %s""",
    (batch_size,),
)
```
Use `%s` placeholders with `params` tuple. This is the psycopg v3 convention.

**F-string SQL (anti-pattern found):**
- `titan/a2a_server.py:90` — f-string with `{where}` clause
- `hermes/web/app.py:921` — f-string in SELECT
- `openjarvis/core/decisions.py:145,195` — f-string WHERE clauses
These are SQL injection risks if `where` is user-controlled. New code MUST use parameterized queries.

**JSONB writes:**
Use `psycopg.types.json.Jsonb()` wrapper for JSONB columns:
```python
from psycopg.types.json import Jsonb
await execute("INSERT INTO events (payload) VALUES (%s)", (Jsonb(payload_dict),))
```

**Connection pool:**
`shared/db.py` manages a global `AsyncConnectionPool` (2-10 connections). Init with `init_pool()` at daemon startup, close with `close_pool()` at shutdown.

## Configuration Patterns

**Frozen dataclasses from env vars:**
```python
# shared/config.py:33-43
@dataclass(frozen=True)
class PostgresConfig:
    user: str = _env("POSTGRES_USER", "perseus")
    password: str = _env("POSTGRES_PASSWORD")
    db: str = _env("POSTGRES_DB", "perseus")
    host: str = _env("POSTGRES_HOST", "localhost")
    port: int = _env_int("POSTGRES_PORT", 5432)
```
All config sections are frozen dataclasses. Access via `config.postgres.dsn`, `config.claude.api_key`, etc.

**Runtime config via DB (system_config table):**
```python
override = await get_config("model_primary", None)  # Returns None if not set
await set_config("shadow_mode", True)
```
The `system_config` table holds runtime-changeable settings (shadow mode, model overrides, feature flags). Dashboard-editable via War Room.

**Config hierarchy:** `.env` -> frozen dataclass defaults -> `system_config` DB overrides (for specific keys like model selection).

## Import Organization

**Order (enforced by ruff `I` rules):**
1. Standard library (`import asyncio`, `import json`, `import logging`)
2. Third-party (`import httpx`, `from psycopg.rows import dict_row`)
3. Local/project (`from shared.db import fetch_all`, `from titan.memory import get_relevant_learnings`)

**No path aliases** — all imports use relative package names: `shared.db`, `titan.pipeline.lead_discovery`, `tools.firecrawl_client`.

## Observability

**Sentry integration (optional):**
`shared/observability.py` — initializes Sentry SDK if available, provides `capture_exception()` helper. Falls back to no-op stubs when sentry-sdk is not installed.

**Prometheus metrics (optional):**
`shared/observability.py` — Counter, Gauge, Histogram from prometheus_client, with no-op stubs as fallback. `observe_db_query()` tracks query latency.

**Pipeline error events:**
`shared/pipeline_alerts.py:emit_pipeline_error()` emits structured events with stage, error type, lead_id. These surface in Hermes War Room.

## Anti-Patterns Found

**Silent exception swallowing:**
- `perseus/misalignment_probe.py:156,257,356` — bare `except Exception:` returning safe defaults without logging
- `perseus/sleep_cycle.py` — 10+ instances of `except Exception` with only debug-level logging
- `perseus/daemon.py:215` — `except Exception: pass`
New code MUST log at minimum `logger.warning()` when catching broadly.

**Sync `asyncio.run()` in tests:**
Many test files use `asyncio.run(coro)` instead of `async def test_*` with pytest-asyncio. This works but prevents proper async fixture injection. New tests SHOULD use `async def` test functions.

**sys.modules injection in tests:**
284 occurrences across 47 test files of `sys.modules[...] = fake_module`. This is the dominant mocking strategy but causes test isolation issues (see conftest.py module isolation guard). Prefer `unittest.mock.patch` where possible.

**JSON extraction via string slicing:**
The `result.find("{")` / `result.rfind("}")` pattern is used across pipeline stages. Fragile when LLM returns markdown-wrapped JSON or multiple JSON objects.

**F-string SQL:**
Found in `titan/a2a_server.py`, `hermes/web/app.py`, `openjarvis/core/decisions.py`. Use parameterized queries instead.

---

*Convention analysis: 2026-03-29*
