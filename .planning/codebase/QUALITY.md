# Codebase Quality Analysis

**Analysis Date:** 2026-03-27

## 1. Test Coverage

### Test Volume
- **~22 top-level test files** in `tests/` covering Perseus daemon business logic
- **~120+ OpenJarvis test files** in `tests/openjarvis/` covering the orchestrator framework (agents, channels, CLI, core, engine, tools, security, etc.)
- **Total reported: 579+ tests** (per project memory from Phase 0-7 completion)

### What IS Tested (Good Coverage)

**Core business logic:**
- State machine transitions: `tests/test_state_machine.py` — exhaustive valid/invalid/terminal state coverage
- Budget gate (LLM cost control): `tests/test_budget_gate.py` — threshold, exceeded, fail-open scenarios
- Email compliance (CAN-SPAM): `tests/test_compliance.py` — unsubscribe links, signature bits, footer injection, send logging, failure marking, duplicate prevention
- DB transactions: `tests/test_db_transaction.py` — connection + transaction context ordering
- Graceful shutdown: `tests/test_graceful_shutdown.py` — work drain, stale task requeue, daemon instrumentation verification
- Pipeline error alerting: `tests/test_pipeline_error_alerting.py` — verifies all 8 pipeline stages emit errors, Hermes formats them
- Task deduplication: `tests/test_task_dedupe.py`
- Telegram auth: `tests/test_telegram_auth.py`

**OpenJarvis framework:**
- Agent types (react, executor, manager, orchestrator, scheduler, rlm): extensive test suites in `tests/openjarvis/agents/`
- All channel integrations (Telegram, Discord, Slack, WhatsApp, IRC, Matrix, etc.): `tests/openjarvis/channels/`
- CLI commands: `tests/openjarvis/cli/`
- Core config, registry, events, types: `tests/openjarvis/core/`
- Security (injection scanner, SSRF, signing): `tests/openjarvis/security/`
- Tools (file, git, http, calculator, browser): `tests/openjarvis/tools/`

### What is NOT Tested (Coverage Gaps)

**High-risk untested areas:**
- `conway/wallet.py` — Ethereum wallet operations (balance, transfer, keystore) have NO dedicated tests. This handles real USDC on Base L2.
- `conway/x402_client.py` — Micropayment client, no tests
- `conway/ledger.py`, `conway/survival.py` — Economic enforcement, no tests
- `tools/payment_router.py` — Stripe + Wise payment routing, no tests
- `tools/instantly_client.py` — Email provider client (partial test via `test_instantly_client.py` but compliance tests mock it)
- `hermes/telegram_bot.py` — Telegram command handlers, no unit tests
- `hermes/web/app.py` — FastAPI War Room dashboard, no API endpoint tests
- `hermes/web/operator_chat.py`, `hermes/web/insights.py` — No tests
- `shared/magma.py` — MAGMA learning engine (~1400 lines), no dedicated tests
- `shared/hybrid_rag.py` — RAG pipeline, no tests
- `shared/email_enrichment.py` — No tests

**Medium-risk untested areas:**
- `titan/pipeline/lead_discovery.py` — Discovery logic tested indirectly (source verification) but no unit tests for the `discover_leads()` function itself
- `titan/pipeline/build_site.py` — Site building pipeline, no tests
- `titan/pipeline/close_deal.py` — Deal closing, no dedicated tests
- `clawdbot/site_builder.py`, `clawdbot/brain.py` — AI site construction, no tests
- `perseus/scheduler.py` — 15 scheduled task definitions, no dedicated tests
- `shared/observability.py` — Metrics and Sentry integration, no tests

### Test Infrastructure

**Framework:** pytest >= 8 with pytest-asyncio (auto mode), pytest-cov, respx
**Config:** `pyproject.toml` `[tool.pytest.ini_options]`
**Run command:** `PYTHONPATH=. python3 -m pytest tests/ -v`

**Test isolation strategy:**
- `tests/conftest.py` provides `FakeDB` in-memory mock and `patch_db` fixture
- Module isolation guard in conftest protects against `sys.modules` poisoning across test files (both collection-time and execution-time snapshots)
- `tests/openjarvis/conftest.py` clears all registries and event bus between tests (autouse fixture)

**Mocking patterns:**
- DB layer mocked at `shared.db` (single chokepoint design)
- Some tests use `sys.modules` injection for heavy dependencies (psycopg, psycopg_pool) — see `tests/test_db_transaction.py`
- Some tests use source code assertion (`Path.read_text()` + `assert "pattern" in code`) as lightweight contract tests — see `tests/test_pipeline_error_alerting.py`, `tests/test_graceful_shutdown.py`

**Test markers:** `live` (running engine), `cloud` (API keys), `nvidia`, `apple`, `slow`

## 2. Error Handling Patterns

### Good Patterns (Follow These)

**DB layer — errors propagated with observability:**
```python
# shared/db.py — errors are re-raised after metrics + Sentry capture
except Exception as exc:
    observe_db_query(operation, time.perf_counter() - started_at, success=False)
    capture_exception(exc, service_name="db", category="query", extra_context={"operation": operation})
    raise
```

**Budget gate — fail open for revenue protection:**
```python
# shared/llm_client.py — budget check failure allows the call through
except Exception as e:
    logger.warning(f"Budget check failed (allowing call): {e}")
return requested_model
```

**Daemon main loops — crash capture + re-raise:**
```python
# titan/daemon.py, hermes/daemon.py — top-level exception capture
except Exception as exc:
    capture_exception(exc, service_name="titan", category="main")
    logger.exception("Titan crashed")
    raise
```

**Task failure — retry with dead-letter queue:**
```python
# shared/agent_base.py — 3 retries then dead_letter + urgent alert
SET retry_count = COALESCE(retry_count, 0) + 1,
    status = CASE WHEN COALESCE(retry_count, 0) + 1 >= 3 THEN 'dead_letter' ELSE 'failed' END
```

### Concerning Patterns (Avoid These)

**Silent exception swallowing in optional features:**
```python
# shared/milestone_rewards.py — multiple bare except-pass blocks
except Exception:
    pass  # Swallows MAGMA, TTRL, and bandit errors silently
```

**EventBus publish failures silently dropped:**
```python
# shared/agent_base.py lines 128-130
except Exception:
    pass  # EventBus failure is silent
```

**A2A forwarding failures silently dropped:**
```python
# shared/agent_base.py lines 136-137
except Exception:
    pass  # DB fallback catches it on Hermes's poll cycle
```

**Hermes active forward loop catches all errors at debug level:**
```python
# hermes/daemon.py line 187
except Exception as e:
    logger.debug(f"Active forward loop: {e}")  # Should be warning for persistent failures
```

### Error Handling Summary
- **Core data path (DB, LLM, pipeline):** Errors properly propagated and captured
- **Optional/enhancement features (MAGMA, bandits, EventBus, A2A forwarding):** Errors silently swallowed with bare `except: pass`
- **Daemon lifecycle:** Proper signal handling, graceful shutdown with work drain
- **Async exception handler:** Installed on event loop for unhandled exceptions (`shared/observability.py`)

## 3. Code Smells and Anti-Patterns

### sys.modules Injection in Tests
Multiple test files manually inject fake modules via `sys.modules`:
- `tests/test_db_transaction.py` — creates fake psycopg
- `tests/test_compliance.py` — creates fake shared.db + psycopg
- `tests/test_budget_gate.py` — creates fake shared.db
- `tests/test_graceful_shutdown.py` — creates fake shared.db

The conftest module isolation guard (`_PROTECTED_MODULES`) mitigates cross-test contamination, but this pattern is fragile and hard to maintain.

### Inline Imports
Many files use runtime imports inside functions:
- `titan/daemon.py` — `import inspect` inside `_invoke_handler`
- `shared/comms.py` — `from shared.capability_router import get_capability_router` inside `request_task`
- `shared/agent_base.py` — `from shared.oj_bridge import get_bus` inside `__init__`
- `hermes/daemon.py` — `from shared.llm_client import llm` inside loop body

This is intentional for lazy loading (avoid import-time failures when optional dependencies are missing) but makes dependency graphs opaque.

### Source Code Assertion Tests
Several tests read source files and assert string patterns exist:
```python
# tests/test_pipeline_error_alerting.py
code = path.read_text()
assert "emit_pipeline_error(" in code
```
These are brittle — they break on refactoring even if behavior is preserved. Used as contract enforcement where full integration tests would be expensive.

### Singleton Patterns
- `shared/config.py` — `config = PerseusConfig()` (module-level singleton)
- `shared/llm_client.py` — `llm = LLMClient()` (module-level singleton)
- `shared/db.py` — `_pool` global with lock

These work but make testing harder (requires sys.modules manipulation).

## 4. Dead Code and Unused Modules

### Confirmed Removed
- `titan/daemon.py` line 375-381 — Comment documents removal of `_run_pipeline_cycle` (duplicated pipeline execution)

### Potentially Underused Modules
- `shared/deep_thinking.py` — "Deep thinking" module, unclear if actively called
- `shared/scientific_loop.py` — Scientific loop pattern, unclear usage
- `shared/adaptive_dashboard.py` — Adaptive dashboard, unclear integration
- `shared/prospect_simulator.py` — Prospect simulation, unclear usage
- `shared/debate_qd.py` — Quality-diversity debate, unclear usage
- `shared/metaclaw.py` — MetaClaw system, unclear active usage
- `shared/self_model.py` — Self-model module, unclear usage
- `perseus/cell_division.py` — Agent spawning, listed as aspirational in audit
- `perseus/backprop.py` — Backpropagation learning, unclear if active
- `perseus/scout.py` — Scout functionality, unclear status

### Vendored Tool Directories
- `tools/browser-use/` — Full browser-use library vendored (~50+ TODO/FIXME comments)
- `tools/firecrawl/` — Full Firecrawl SDK vendored
- Both excluded from ruff linting via `extend-exclude` in pyproject.toml

## 5. Configuration Management

### Good: Environment-Driven Configuration
`shared/config.py` uses a clean dataclass-based config system:
- All settings loaded from `.env` via `python-dotenv`
- Typed access: `config.postgres.dsn`, `config.claude.api_key`, `config.budget.monthly_cap`
- 15 config sections covering all subsystems
- Sensible defaults for every setting
- `frozen=True` dataclasses prevent runtime mutation

### Good: Runtime Config Overrides via DB
`shared/db.py` provides `get_config`/`set_config` for runtime overrides:
- LLM model selection overridable from dashboard
- Titan pause/unpause via `titan_paused` config key
- Discovery strategy overrides

### Concern: Config Singleton Created at Import Time
```python
# shared/config.py line 214
config = PerseusConfig()
```
All env vars are read when the module is first imported. This means:
- Tests that set env vars AFTER import get stale values
- No way to reload config without reloading the module

### Concern: Some Hardcoded Values
- `hermes/daemon.py` line 21-22: `DASHBOARD_HOST`, `DASHBOARD_PORT` read from env but with hardcoded defaults
- `shared/llm_client.py` line 47-51: Cost-per-1K-token estimates hardcoded in source
- `titan/daemon.py` line 138: `_cycle_interval = 30` hardcoded (not configurable)
- Various A2A port numbers hardcoded as fallback defaults

## 6. Known Issues and Technical Debt

### From Project Memory (Audit Findings)
- **32 known bugs**: 4 P0 (money/security), 11 P1 (blocks operation), 17 P2 (reliability)
- **Wallet column swap** in Conway — incorrect column ordering in DB writes
- **Keystore passwords** — security concern in Conway key management
- **JSONB writes** — incorrect serialization in some paths
- **Missing A2A server** — some agents lack A2A endpoints
- **Sync blocking** — synchronous calls in async context
- **Hollow stubs** — functions that return placeholder values

### mypy Configuration
```toml
# pyproject.toml
[[tool.mypy.overrides]]
module = "openjarvis.*"
ignore_errors = true
```
The entire OpenJarvis framework skips mypy type checking. Also `shared.observability` ignores errors. This masks type safety issues in a large portion of the codebase.

### Test Isolation Fragility
The `conftest.py` module isolation guard is a sophisticated workaround for a fundamental problem: tests that manipulate `sys.modules` at module level. This is well-engineered (both collection-time and execution-time guards) but indicates the test suite is fragile if the protected modules list gets out of date.

### ruff Configuration Permissiveness
```toml
ignore = ["E501", "E741", "UP042"]
```
Line length enforcement is disabled (E501). Per-file ignores for tests are broad.

## 7. Production Readiness Signals

### Logging: GOOD
- `shared/logging_config.py` — Structured logging with JSON formatter option
- `RotatingFileHandler` with configurable max size and backup count
- Trace context injection via `shared/observability.py` (trace_id, correlation_id, task_id)
- Per-agent log files in configurable `logs/` directory
- Log level configurable via `LOG_LEVEL` env var

### Monitoring: GOOD
- `shared/observability.py` — Prometheus metrics (counters, gauges, histograms)
  - Agent starts/shutdowns, events emitted, task claims/completions/failures
  - Active in-flight work gauge, work duration histogram
  - DB query duration histogram by operation
  - Exception counter by service and category
- Sentry integration for error aggregation (optional, enabled via `SENTRY_DSN`)
- Per-service metrics ports (orchestrator:9100, titan:9101, hermes:9102, clawdbot:9103)
- NoOp stubs when prometheus_client/sentry_sdk not installed — graceful degradation

### Graceful Shutdown: GOOD
- `shared/agent_base.py` — `begin_work`/`finish_work` tracking with drain timeout
- Signal handlers (SIGTERM, SIGINT) installed in all daemon `main()` functions
- Stale task requeue on restart (`requeue_stale_tasks`)
- Dead-letter queue for tasks that fail 3 times with urgent alert emission
- `finalize_shutdown` deregisters agent and closes DB pool

### Health Checks: PRESENT
- `AgentBase.health_check()` abstract method on all agents
- `health_check` task type in Titan's task handlers
- OpenJarvis vassal registry heartbeat integration

### Database Resilience: GOOD
- Connection pool with retry logic (5 retries, exponential backoff) in `shared/db.py`
- Pool lock prevents double initialization
- Parameterized queries throughout (SQL injection protection)
- Transaction support via `async with db.transaction()`

### Missing Production Signals
- **No circuit breaker** on external API calls (Anthropic, Ollama, Instantly, Firecrawl)
- **No request rate limiting** on the FastAPI dashboard endpoints
- **No health check HTTP endpoint** — health is DB-based, not HTTP-based
- **No structured alerting escalation** — all alerts go to Telegram with equal priority
- **No formal SLA monitoring** for the 5-second alert latency target or 60-second pipeline latency target

---

*Quality analysis: 2026-03-27*
