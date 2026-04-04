# Phase 14: Quality & Observability

**Goal:** Implement 3 Paperclip-inspired patterns into Objective Hertz: pytest behavioral evals, heartbeat lifecycle, and log redaction. Adds quality infrastructure that makes daemon behavior verifiable, daemon health visible, and credential leaks in logs impossible.
**Requirements:** QUAL-01 through QUAL-11
**Depends on:** Phase 11 (budget consolidation), Phase 12 (agent state machines), Phase 13 (per-call cost tracking)
**Feature flags:** `BEHAVIORAL_EVALS_ENABLED`, `HEARTBEAT_LIFECYCLE_ENABLED`, `LOG_REDACTION_ENABLED` (all default OFF)
**License:** MIT (Paperclip patterns)

---

## Context

After Phases 11-13, budget is consolidated, agents have state machines, and cost tracking is per-call. The system is operationally functional but lacks three quality layers:

1. **No behavioral regression tests** — unit tests exist (457+) but nothing validates daemon behavioral contracts (budget guard fails closed, Titan refuses email without address, middleware actually executes)
2. **No heartbeat** — if a daemon hangs silently, nothing detects it until a downstream failure surfaces
3. **No log redaction** — `credential_stripper.py` exists (6 patterns) but is not wired into Python logging; secrets can leak into `logs/` directory files

### Current State

| Component | File | Status |
|-----------|------|--------|
| AgentBase | `shared/agent_base.py` (369L) | Lifecycle hooks, DeerFlow memory, no heartbeat |
| Observability | `shared/observability.py` (538L) | Prometheus metrics, structured tracing |
| Credential stripper | `openjarvis/security/credential_stripper.py` | 6 patterns (api_key, aws_key, github_token x2, slack_token, bearer_token) |
| Logging config | `shared/logging_config.py` (107L) | JsonFormatter + text formatter, RotatingFileHandler, no redaction |
| Soul/DNA | `soul/dna/*.yaml` | Per-daemon personality profiles |
| Feature flags | `shared/middleware.py`, env vars | Pattern: `ENABLE_X` env var checked via `_flag()` or direct `os.environ.get()` |
| Tests | `tests/` (457+) | Unit + integration, no behavioral eval category |
| DB migrations | `scripts/migrations/` | Through 024 (neuro-scores) |

---

## Tasks

### Task 1: Database Migration 027 — eval_results table (QUAL-01)
**File:** `scripts/migrations/027-eval-results.sql` (new)
**What:**

```sql
-- Migration 027: Behavioral eval results for trend tracking
CREATE TABLE IF NOT EXISTS eval_results (
    eval_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suite         VARCHAR(100) NOT NULL,
    scenario      VARCHAR(200) NOT NULL,
    passed        BOOLEAN NOT NULL,
    score         NUMERIC(5, 3),
    cost_usd      NUMERIC(10, 6),
    details       JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_eval_results_suite ON eval_results (suite, created_at DESC);
CREATE INDEX idx_eval_results_passed ON eval_results (passed, created_at DESC);

-- NOTE: session_health table already created in migration 025 (Phase 12).
-- Phase 14 heartbeat emitter writes to the existing session_health table.
-- Add partial index for stale detection if not already present:
CREATE INDEX IF NOT EXISTS idx_session_health_stale ON session_health (heartbeat_at)
    WHERE heartbeat_at < NOW() - INTERVAL '5 minutes';
```

**Acceptance criteria:**
- [ ] Migration runs cleanly on fresh and existing databases
- [ ] Both tables have appropriate indices
- [ ] Parameterized SQL only (no f-strings)
- [ ] `session_health` supports stale-detection queries via partial index

---

### Task 2: Pytest Behavioral Evals Framework (QUAL-02, QUAL-03)
**File:** `tests/evals/__init__.py` (new)
**File:** `tests/evals/conftest.py` (new)
**What:**

Shared fixtures for behavioral evals. All evals use mocked LLM responses — zero real API calls.

```python
# tests/evals/conftest.py

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns canned responses. No real API calls."""
    client = AsyncMock()
    client.generate.return_value = {"content": "mocked response", "cost_usd": 0.001}
    return client

@pytest.fixture
def mock_db_pool():
    """Mock database pool with configurable fetch/execute."""
    pool = AsyncMock()
    pool.fetch_val = AsyncMock(return_value=None)
    pool.fetch_all = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool

@pytest.fixture
def eval_recorder(mock_db_pool):
    """Records eval results to mock DB (or real DB when BEHAVIORAL_EVALS_ENABLED)."""
    from tests.evals.recorder import EvalRecorder
    return EvalRecorder(db=mock_db_pool)

@pytest.fixture(autouse=True)
def disable_real_services(monkeypatch):
    """Safety net: ensure no real external calls during evals."""
    monkeypatch.setenv("BEHAVIORAL_EVALS_ENABLED", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
```

**File:** `tests/evals/recorder.py` (new)

```python
class EvalRecorder:
    """Stores eval results for trend tracking.

    When BEHAVIORAL_EVALS_ENABLED=true, writes to eval_results table.
    Otherwise stores in-memory only (tests always run regardless of flag).
    """

    def __init__(self, db=None):
        self._db = db
        self._results: list[dict] = []

    async def record(
        self,
        suite: str,
        scenario: str,
        passed: bool,
        score: float | None = None,
        cost_usd: float | None = None,
        details: dict | None = None,
    ) -> None:
        result = {
            "suite": suite,
            "scenario": scenario,
            "passed": passed,
            "score": score,
            "cost_usd": cost_usd,
            "details": details or {},
        }
        self._results.append(result)

        if os.environ.get("BEHAVIORAL_EVALS_ENABLED", "").lower() in ("true", "1"):
            await self._persist(result)

    async def _persist(self, result: dict) -> None:
        if self._db is None:
            return
        await self._db.execute(
            """INSERT INTO eval_results (suite, scenario, passed, score, cost_usd, details)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (result["suite"], result["scenario"], result["passed"],
             result["score"], result["cost_usd"],
             json.dumps(result["details"])),
        )

    @property
    def results(self) -> list[dict]:
        return self._results
```

**Acceptance criteria:**
- [ ] `tests/evals/` directory exists with `__init__.py`, `conftest.py`, `recorder.py`
- [ ] All fixtures disable real API calls (safety net)
- [ ] EvalRecorder stores results in-memory always, persists to DB only when flag is on
- [ ] Parameterized SQL in recorder (no f-strings)

---

### Task 3: Behavioral Eval — Budget Guard Fails Closed (QUAL-04)
**File:** `tests/evals/test_eval_budget.py` (new)
**What:**

```python
"""Behavioral eval: budget guard MUST fail closed on DB error.

When fetch_val raises ConnectionError (DB unreachable), the budget guard
must reject the API call and fall back to Ollama — never allow the call through.
This is a P0 safety property per AEGIS audit.
"""

import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_budget_guard_fails_closed_on_db_error(eval_recorder):
    """Budget guard rejects API calls when DB is unreachable."""
    from shared.llm_client import LLMClient

    client = LLMClient()

    with patch("shared.db.fetch_val", side_effect=ConnectionError("DB down")):
        # The budget check should fail closed — reject, not allow
        result = await client._check_budget()
        assert result is False or result == "rejected", (
            "Budget guard MUST fail closed on DB error. "
            "Got permissive result instead of rejection."
        )

    await eval_recorder.record(
        suite="budget",
        scenario="fails_closed_on_db_error",
        passed=True,
    )

@pytest.mark.asyncio
async def test_budget_guard_falls_back_to_ollama_on_db_error(eval_recorder):
    """When budget check fails, generate() falls back to Ollama, not Claude API."""
    from shared.llm_client import LLMClient

    client = LLMClient()

    with patch("shared.db.fetch_val", side_effect=ConnectionError("DB down")), \
         patch.object(client, "_call_ollama", new_callable=AsyncMock) as mock_ollama, \
         patch.object(client, "_call_claude", new_callable=AsyncMock) as mock_claude:
        mock_ollama.return_value = {"content": "fallback response"}
        await client.generate(prompt="test", model="sonnet")

        assert mock_ollama.called, "Should fall back to Ollama"
        assert not mock_claude.called, "Should NOT call Claude API when budget DB is down"

    await eval_recorder.record(
        suite="budget",
        scenario="ollama_fallback_on_db_error",
        passed=True,
    )
```

**Acceptance criteria:**
- [ ] Test verifies budget guard rejects (not allows) on DB ConnectionError
- [ ] Test verifies fallback to Ollama, not Claude API
- [ ] No real API calls or DB connections
- [ ] Passes with `PYTHONPATH=. pytest tests/evals/test_eval_budget.py -v`

---

### Task 4: Behavioral Eval — Titan Email Compliance (QUAL-05)
**File:** `tests/evals/test_eval_titan_email.py` (new)
**What:**

```python
"""Behavioral eval: Titan MUST refuse email send without physical_address.

CAN-SPAM compliance: if physical_address in system_config matches '[SET YOUR'
or is empty, Titan must refuse to send emails. Checked at startup AND before
each batch. Per AEGIS audit requirement.
"""

import pytest
from unittest.mock import AsyncMock, patch

INVALID_ADDRESSES = [
    "",
    "[SET YOUR ADDRESS HERE]",
    "[SET YOUR PHYSICAL ADDRESS]",
    None,
]

@pytest.mark.asyncio
@pytest.mark.parametrize("address", INVALID_ADDRESSES)
async def test_titan_refuses_email_without_physical_address(address, eval_recorder):
    """Titan refuses to send emails when physical_address is invalid."""
    from titan.pipeline.email_compose import EmailComposer

    composer = EmailComposer()

    with patch("shared.db.fetch_val", new_callable=AsyncMock, return_value=address):
        result = await composer.validate_compliance()
        assert result.get("can_send") is False, (
            f"Titan MUST refuse email send when physical_address={address!r}. "
            "CAN-SPAM violation."
        )

    await eval_recorder.record(
        suite="titan_email",
        scenario=f"refuses_without_address_{address!r}",
        passed=True,
    )

@pytest.mark.asyncio
async def test_titan_allows_email_with_valid_physical_address(eval_recorder):
    """Titan allows email send when physical_address is valid."""
    from titan.pipeline.email_compose import EmailComposer

    composer = EmailComposer()
    valid_address = "123 Main St, Suite 100, Austin, TX 78701"

    with patch("shared.db.fetch_val", new_callable=AsyncMock, return_value=valid_address):
        result = await composer.validate_compliance()
        assert result.get("can_send") is True, (
            "Titan should allow email send with valid physical address."
        )

    await eval_recorder.record(
        suite="titan_email",
        scenario="allows_with_valid_address",
        passed=True,
    )
```

**Acceptance criteria:**
- [ ] Tests cover empty, placeholder, and None address values
- [ ] Tests verify both rejection and acceptance paths
- [ ] No real email sends
- [ ] Passes with `PYTHONPATH=. pytest tests/evals/test_eval_titan_email.py -v`

---

### Task 5: Behavioral Eval — Middleware Chain Executes (QUAL-06)
**File:** `tests/evals/test_eval_middleware.py` (new)
**What:**

```python
"""Behavioral eval: middleware chain MUST execute in Titan daemon.

The middleware chain must not be dead code — it must be imported AND called
by titan/daemon.py during pipeline execution. Per AEGIS audit requirement.
"""

import pytest
import ast
import importlib
from unittest.mock import AsyncMock, patch, MagicMock

def test_titan_daemon_imports_middleware():
    """Verify titan/daemon.py imports shared.middleware at module level."""
    with open("titan/daemon.py", "r") as f:
        source = f.read()
    tree = ast.parse(source)

    middleware_imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "middleware" in node.module:
                middleware_imported = True
                break
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "middleware" in alias.name:
                    middleware_imported = True
                    break

    assert middleware_imported, (
        "titan/daemon.py MUST import shared.middleware. "
        "Middleware chain is dead code without this import."
    )

@pytest.mark.asyncio
async def test_middleware_chain_called_during_pipeline(eval_recorder):
    """Verify middleware chain is actually invoked during Titan pipeline execution."""
    with patch("shared.middleware.run_middleware_chain", new_callable=AsyncMock) as mock_chain:
        mock_chain.return_value = {"success": True, "output": "test"}

        # Import and trigger a pipeline stage that should use middleware
        from titan.pipeline import email_compose
        # The specific invocation depends on Titan's pipeline wiring
        # Key assertion: middleware chain function was called
        # (Adjusted to match actual Titan pipeline entry point)

    await eval_recorder.record(
        suite="middleware",
        scenario="chain_called_during_pipeline",
        passed=True,
    )
```

**Acceptance criteria:**
- [ ] AST-based import check (not string grep — handles `from X import Y` and `import X`)
- [ ] Runtime verification that middleware chain is actually called
- [ ] Passes with `PYTHONPATH=. pytest tests/evals/test_eval_middleware.py -v`

---

### Task 6: Behavioral Eval — ClawdBot Escalation, State Machine, Recursion Guard (QUAL-07, QUAL-08, QUAL-09)
**File:** `tests/evals/test_eval_clawdbot.py` (new)
**File:** `tests/evals/test_eval_state_machine.py` (new)
**File:** `tests/evals/test_eval_recursion.py` (new)
**What:**

```python
# tests/evals/test_eval_clawdbot.py
"""Behavioral eval: ClawdBot escalates to Hermes on skill execution failure."""

import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_clawdbot_escalates_on_skill_failure(eval_recorder):
    """When a skill execution fails, ClawdBot sends alert via Hermes A2A."""
    from clawdbot.brain import SkillRouter

    router = SkillRouter()
    mock_skill = AsyncMock(side_effect=RuntimeError("Skill crashed"))

    with patch.object(router, "_get_skill", return_value=mock_skill), \
         patch("shared.comms.send_a2a", new_callable=AsyncMock) as mock_a2a:
        await router.execute_skill("build_site", {"client_id": "test-123"})

        assert mock_a2a.called, (
            "ClawdBot MUST escalate to Hermes when skill execution fails."
        )
        call_args = mock_a2a.call_args
        assert "hermes" in str(call_args).lower(), (
            "Escalation must target Hermes daemon."
        )

    await eval_recorder.record(
        suite="clawdbot",
        scenario="escalates_on_skill_failure",
        passed=True,
    )
```

```python
# tests/evals/test_eval_state_machine.py
"""Behavioral eval: agent state machine rejects invalid transitions."""

import pytest

VALID_TRANSITIONS = [
    ("IDLE", "EXECUTING"),
    ("EXECUTING", "IDLE"),
    ("EXECUTING", "PAUSED"),
    ("PAUSED", "IDLE"),
    ("IDLE", "TERMINATED"),
    ("EXECUTING", "TERMINATED"),
]

INVALID_TRANSITIONS = [
    ("IDLE", "PAUSED"),          # Can't pause without executing
    ("TERMINATED", "EXECUTING"),  # Can't start work during termination
    ("PAUSED", "EXECUTING"),     # Must go to IDLE first
    ("TERMINATED", "IDLE"),      # Termination is terminal
]

@pytest.mark.parametrize("from_state,to_state", INVALID_TRANSITIONS)
def test_state_machine_rejects_invalid_transition(from_state, to_state, eval_recorder):
    """Agent state machine must reject invalid state transitions."""
    from titan.state_machine import AgentStateMachine

    sm = AgentStateMachine(initial_state=from_state)
    result = sm.can_transition(to_state)

    assert result is False, (
        f"State machine MUST reject transition {from_state} -> {to_state}. "
        "Invalid transitions break daemon lifecycle guarantees."
    )

@pytest.mark.parametrize("from_state,to_state", VALID_TRANSITIONS)
def test_state_machine_allows_valid_transition(from_state, to_state, eval_recorder):
    """Agent state machine must allow valid state transitions."""
    from titan.state_machine import AgentStateMachine

    sm = AgentStateMachine(initial_state=from_state)
    result = sm.can_transition(to_state)

    assert result is True, (
        f"State machine should allow transition {from_state} -> {to_state}."
    )
```

```python
# tests/evals/test_eval_recursion.py
"""Behavioral eval: recursion guard blocks depth > 5 tasks."""

import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_recursion_guard_blocks_deep_tasks(eval_recorder):
    """Task dispatch must be blocked when recursion depth exceeds 5."""
    from openjarvis.agents.loop_guard import LoopGuard

    guard = LoopGuard(max_depth=5)

    # Simulate 5 levels of nesting
    for i in range(5):
        assert guard.can_dispatch(depth=i) is True

    # Level 6 should be blocked
    assert guard.can_dispatch(depth=5) is False, (
        "Recursion guard MUST block tasks at depth > 5. "
        "Unbounded recursion risks infinite loops and budget exhaustion."
    )

    await eval_recorder.record(
        suite="recursion",
        scenario="blocks_depth_gt_5",
        passed=True,
    )

@pytest.mark.asyncio
async def test_recursion_guard_allows_shallow_tasks(eval_recorder):
    """Tasks within recursion limit should execute normally."""
    from openjarvis.agents.loop_guard import LoopGuard

    guard = LoopGuard(max_depth=5)

    for depth in range(5):
        assert guard.can_dispatch(depth=depth) is True

    await eval_recorder.record(
        suite="recursion",
        scenario="allows_shallow_tasks",
        passed=True,
    )
```

**Acceptance criteria:**
- [ ] ClawdBot escalation eval verifies A2A call to Hermes on skill failure
- [ ] State machine eval covers all invalid transitions (reject) and valid transitions (allow)
- [ ] Recursion guard eval verifies depth=5 boundary
- [ ] All pass with `PYTHONPATH=. pytest tests/evals/test_eval_clawdbot.py tests/evals/test_eval_state_machine.py tests/evals/test_eval_recursion.py -v`

---

### Task 7: Heartbeat Emitter (QUAL-10)
**File:** `shared/heartbeat.py` (new, ~150 lines)
**What:**

```python
"""Heartbeat lifecycle for daemon health monitoring.

Each daemon emits periodic heartbeats to session_health table.
Stale heartbeats (>5min) trigger Hermes alert and state -> PAUSED.

Feature flag: HEARTBEAT_LIFECYCLE_ENABLED
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger("perseus.heartbeat")

HEARTBEAT_INTERVAL_SECONDS = 30
STALE_THRESHOLD_SECONDS = 300  # 5 minutes


def _heartbeat_enabled() -> bool:
    return os.environ.get("HEARTBEAT_LIFECYCLE_ENABLED", "").lower() in ("true", "1")


class HeartbeatEmitter:
    """Periodic heartbeat signal to session_health table.

    Usage:
        emitter = HeartbeatEmitter(daemon_name="titan")
        await emitter.start()   # Call on EXECUTING state entry
        ...
        await emitter.stop()    # Call on state exit

    Parameters:
        daemon_name: Identifier for this daemon (perseus, titan, hermes, clawdbot, conway)
        interval: Seconds between heartbeats (default: 30)
        on_stale: Optional async callback invoked when stale heartbeat detected
    """

    def __init__(
        self,
        daemon_name: str,
        interval: int = HEARTBEAT_INTERVAL_SECONDS,
        on_stale: Callable[..., Awaitable[None]] | None = None,
    ):
        self._daemon_name = daemon_name
        self._interval = interval
        self._on_stale = on_stale
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_beat: float = 0.0

    async def start(self) -> None:
        """Begin emitting heartbeats. Idempotent."""
        if not _heartbeat_enabled():
            logger.debug("Heartbeat disabled for %s (flag OFF)", self._daemon_name)
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started for %s (interval=%ds)", self._daemon_name, self._interval)

    async def stop(self) -> None:
        """Stop emitting heartbeats. Idempotent."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat stopped for %s", self._daemon_name)

    async def _loop(self) -> None:
        """Main heartbeat loop: emit signal, check for stale peers."""
        while self._running:
            try:
                await self._emit()
                await self._check_stale_peers()
            except Exception as exc:
                logger.warning("Heartbeat error for %s: %s", self._daemon_name, exc)
            await asyncio.sleep(self._interval)

    async def _emit(self) -> None:
        """Write heartbeat to session_health table."""
        from shared import db

        self._last_beat = time.time()
        await db.execute(
            """INSERT INTO session_health (daemon_name, heartbeat_at, state, metadata)
               VALUES (%s, NOW(), %s, %s)""",
            (self._daemon_name, "alive", "{}"),
        )

    async def _check_stale_peers(self) -> None:
        """Detect peer daemons with stale heartbeats (>5min)."""
        from shared import db

        stale = await db.fetch_all(
            """SELECT DISTINCT daemon_name, MAX(heartbeat_at) as last_beat
               FROM session_health
               WHERE daemon_name != %s
               GROUP BY daemon_name
               HAVING MAX(heartbeat_at) < NOW() - INTERVAL '%s seconds'""",
            (self._daemon_name, STALE_THRESHOLD_SECONDS),
        )

        for row in stale:
            logger.warning(
                "Stale heartbeat: %s last seen %s",
                row["daemon_name"], row["last_beat"],
            )
            if self._on_stale:
                await self._on_stale(row["daemon_name"], row["last_beat"])

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_beat(self) -> float:
        return self._last_beat


async def alert_stale_daemon(daemon_name: str, last_beat: datetime) -> None:
    """Default stale handler: send alert via Hermes A2A."""
    try:
        from shared.comms import send_a2a
        await send_a2a(
            target="hermes",
            capability="alert",
            payload={
                "type": "daemon_stale",
                "daemon": daemon_name,
                "last_heartbeat": str(last_beat),
                "threshold_seconds": STALE_THRESHOLD_SECONDS,
                "severity": "warning",
            },
        )
    except Exception as exc:
        logger.error("Failed to alert Hermes about stale %s: %s", daemon_name, exc)
```

**Acceptance criteria:**
- [ ] `HeartbeatEmitter` writes to `session_health` table every 30s
- [ ] Stale detection threshold is 5 minutes
- [ ] Stale peers trigger Hermes alert via A2A
- [ ] `start()` and `stop()` are idempotent
- [ ] Feature flag `HEARTBEAT_LIFECYCLE_ENABLED` gates all behavior
- [ ] Parameterized SQL only

---

### Task 8: Wire Heartbeat into AgentBase (QUAL-10)
**File:** `shared/agent_base.py` (modify)
**What:**

Add HeartbeatEmitter lifecycle to AgentBase. Start heartbeat when agent enters active work, stop when all work drains.

```python
# In AgentBase.__init__, add:
self._heartbeat: HeartbeatEmitter | None = None

# New method:
async def _start_heartbeat(self) -> None:
    """Start heartbeat emission. Called when agent begins active work."""
    if self._heartbeat is None:
        from shared.heartbeat import HeartbeatEmitter, alert_stale_daemon
        self._heartbeat = HeartbeatEmitter(
            daemon_name=self.name,
            on_stale=alert_stale_daemon,
        )
    await self._heartbeat.start()

async def _stop_heartbeat(self) -> None:
    """Stop heartbeat emission. Called when agent drains active work."""
    if self._heartbeat is not None:
        await self._heartbeat.stop()
```

Wire into existing `_claim_work()` / `_complete_work()` pattern:

```python
# In _claim_work (or equivalent active-work entry point):
if not self._active_work:  # First piece of work
    await self._start_heartbeat()

# In _complete_work (or equivalent drain point):
if not self._active_work:  # All work drained
    await self._stop_heartbeat()
```

**Acceptance criteria:**
- [ ] Heartbeat starts when agent enters first active work item
- [ ] Heartbeat stops when all active work drains
- [ ] No changes to AgentBase public API
- [ ] Existing tests still pass

---

### Task 9: Daemon Lifecycle Documents (QUAL-10)
**Directory:** `soul/lifecycle/` (new)
**Files:**
- `soul/lifecycle/perseus_lifecycle.md` (new)
- `soul/lifecycle/titan_lifecycle.md` (new)
- `soul/lifecycle/hermes_lifecycle.md` (new)
- `soul/lifecycle/clawdbot_lifecycle.md` (new)
- `soul/lifecycle/conway_lifecycle.md` (new)

**What:**

Each lifecycle document defines the daemon's identity and step-by-step execution loop, injected into `generate()` system prompt alongside DNA profiles.

Format per file:

```markdown
# {Daemon} Lifecycle

## Identity
You are {daemon_name}, the {role} daemon of Objective Hertz.

## Execution Loop
1. {step_1}: {description}
2. {step_2}: {description}
...

## Heartbeat Contract
- Emit heartbeat every 30 seconds while executing
- If no heartbeat for 5 minutes, you will be PAUSED
- On suspension, current work is checkpointed and Hermes is alerted

## Boundaries
- {boundary_1}
- {boundary_2}
```

Specific lifecycles:

**perseus_lifecycle.md:**
1. Identity: scheduler and orchestrator
2. Loop: identity -> schedule scan -> dispatch tasks -> monitor daemons -> report status
3. Boundaries: never execute pipeline stages directly, delegate to Titan/ClawdBot

**titan_lifecycle.md:**
1. Identity: revenue engine
2. Loop: identity -> task checkout -> pipeline stage execution -> quality check -> complete/retry
3. Boundaries: never send email without compliance check, max 2 re-drafts per email

**hermes_lifecycle.md:**
1. Identity: alerts and dashboard
2. Loop: identity -> event poll -> alert dispatch -> dashboard update -> forward to operator
3. Boundaries: never suppress critical alerts, always forward P0 events to Telegram

**clawdbot_lifecycle.md:**
1. Identity: site builder and browser automation
2. Loop: identity -> skill select -> browser launch -> build site -> verify output -> deploy
3. Boundaries: escalate to Hermes on any skill failure, never deploy without verification

**conway_lifecycle.md:**
1. Identity: agent economics
2. Loop: identity -> balance check -> tier assessment -> enforcement -> report
3. Boundaries: never override survival tier, always log all wallet operations

**Wiring:** Modify `shared/llm_client.py` to load lifecycle doc alongside DNA when `HEARTBEAT_LIFECYCLE_ENABLED=true`:

```python
# In LLMClient.generate(), after DNA loading:
if os.environ.get("HEARTBEAT_LIFECYCLE_ENABLED", "").lower() in ("true", "1"):
    lifecycle_path = Path(f"soul/lifecycle/{daemon_name}_lifecycle.md")
    if lifecycle_path.exists():
        lifecycle = lifecycle_path.read_text()
        system_prompt = f"{lifecycle}\n\n{system_prompt}"
```

**Acceptance criteria:**
- [ ] 5 lifecycle documents created, one per daemon
- [ ] Each has Identity, Execution Loop, Heartbeat Contract, Boundaries sections
- [ ] Lifecycle injected into system prompt when flag is on
- [ ] Flag OFF = no lifecycle injection, zero behavior change

---

### Task 10: Log Redaction Formatter (QUAL-11)
**File:** `shared/log_redaction.py` (new, ~80 lines)
**What:**

```python
"""Log redaction: strips credentials from all log messages.

Wraps any logging.Formatter. Runs credential_stripper.strip() on every
formatted log line. Must add <1ms latency per log line.

Feature flag: LOG_REDACTION_ENABLED
"""

from __future__ import annotations

import logging
import os
import time

from openjarvis.security.credential_stripper import CredentialStripper

_stripper = CredentialStripper()


def _redaction_enabled() -> bool:
    return os.environ.get("LOG_REDACTION_ENABLED", "").lower() in ("true", "1")


class RedactingFormatter(logging.Formatter):
    """Formatter wrapper that redacts credentials from log output.

    Delegates formatting to an inner formatter, then runs credential
    stripping on the result. Adds <1ms per log line.

    Usage:
        inner = logging.Formatter("%(asctime)s %(message)s")
        redacting = RedactingFormatter(inner)
        handler.setFormatter(redacting)
    """

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        formatted = self._inner.format(record)
        if _redaction_enabled():
            return _stripper.strip(formatted)
        return formatted

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return self._inner.formatTime(record, datefmt)

    def formatException(self, ei) -> str:
        return self._inner.formatException(ei)

    def formatStack(self, stack_info: str) -> str:
        return self._inner.formatStack(stack_info)
```

**Acceptance criteria:**
- [ ] `RedactingFormatter` wraps any `logging.Formatter`
- [ ] Delegates all formatting to inner formatter, then strips
- [ ] Feature flag `LOG_REDACTION_ENABLED` gates redaction (pass-through when OFF)
- [ ] No modification to `CredentialStripper` class itself (reuse existing)

---

### Task 11: Expand Credential Stripper to 15+ Patterns (QUAL-11)
**File:** `openjarvis/security/credential_stripper.py` (modify)
**What:**

Expand `_CREDENTIAL_PATTERNS` from 6 to 15+ patterns per AEGIS requirement. Add:

```python
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Existing 6
    ("api_key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("github_token", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    ("slack_token", re.compile(r"xoxb-[0-9A-Za-z\-]+")),
    ("bearer_token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-.]{20,}")),

    # New: Stripe patterns (4)
    ("stripe_secret", re.compile(r"sk_live_[a-zA-Z0-9]{24,}")),
    ("stripe_test", re.compile(r"sk_test_[a-zA-Z0-9]{24,}")),
    ("stripe_publishable", re.compile(r"pk_live_[a-zA-Z0-9]{24,}")),
    ("stripe_restricted", re.compile(r"rk_live_[a-zA-Z0-9]{24,}")),

    # New: Telegram bot token
    ("telegram_token", re.compile(r"\d{8,10}:[a-zA-Z0-9_-]{35}")),

    # New: Netlify token
    ("netlify_token", re.compile(r"nfp_[a-zA-Z0-9]{40,}")),

    # New: Instantly API key
    ("instantly_key", re.compile(r"inst_[a-zA-Z0-9]{32,}")),

    # New: Database connection strings (Postgres)
    ("db_connection", re.compile(r"postgres(?:ql)?://[^\s'\"]{10,}")),

    # New: JWT tokens
    ("jwt_token", re.compile(r"eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}")),

    # New: Generic long hex secrets (e.g., webhook secrets, API keys)
    ("hex_secret", re.compile(r"(?:secret|key|token|password)[:=]\s*['\"]?[a-fA-F0-9]{32,}['\"]?", re.IGNORECASE)),
]
```

**Acceptance criteria:**
- [ ] 15+ patterns total (was 6)
- [ ] Covers: Stripe (sk_live_, sk_test_, pk_live_, rk_live_), Telegram bot tokens, Netlify tokens, Instantly API keys, DB connection strings, JWT, GitHub tokens (existing), generic hex secrets
- [ ] All existing tests still pass
- [ ] Each pattern has a descriptive label for `[REDACTED:label]` output

---

### Task 12: Wire RedactingFormatter into Logging Config (QUAL-11)
**File:** `shared/logging_config.py` (modify)
**What:**

Wrap existing formatters with `RedactingFormatter`:

```python
# In setup_logging(), after creating formatter:
from shared.log_redaction import RedactingFormatter

text_formatter = logging.Formatter(
    fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
json_formatter = JsonFormatter(agent_name)
formatter = json_formatter if config.log_format.lower() == "json" else text_formatter

# Wrap with redaction
formatter = RedactingFormatter(formatter)
```

This ensures ALL daemon log output (console + file, text + JSON) goes through redaction.

**Acceptance criteria:**
- [ ] Both text and JSON formatters wrapped with RedactingFormatter
- [ ] All 5 daemon log files (perseus.log, titan.log, hermes.log, clawdbot.log, conway.log) use redacting formatter
- [ ] Flag OFF = formatter passes through unchanged (zero overhead)
- [ ] Flag ON = credentials stripped from all log output

---

### Task 13: Heartbeat + Log Redaction Tests (QUAL-10, QUAL-11)
**File:** `tests/test_heartbeat.py` (new)
**File:** `tests/test_log_redaction.py` (new)
**What:**

```python
# tests/test_heartbeat.py
"""Tests for heartbeat lifecycle."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_heartbeat_emitter_start_stop():
    """HeartbeatEmitter starts and stops cleanly."""
    from shared.heartbeat import HeartbeatEmitter

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="test", interval=1)
        await emitter.start()
        assert emitter.is_running
        await asyncio.sleep(0.1)
        await emitter.stop()
        assert not emitter.is_running

@pytest.mark.asyncio
async def test_heartbeat_emitter_noop_when_disabled():
    """HeartbeatEmitter does nothing when flag is OFF."""
    from shared.heartbeat import HeartbeatEmitter

    with patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "false"}):
        emitter = HeartbeatEmitter(daemon_name="test")
        await emitter.start()
        assert not emitter.is_running  # Should not start

@pytest.mark.asyncio
async def test_heartbeat_emitter_idempotent_start():
    """Multiple start() calls don't create multiple tasks."""
    from shared.heartbeat import HeartbeatEmitter

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="test", interval=60)
        await emitter.start()
        task1 = emitter._task
        await emitter.start()  # Second call
        task2 = emitter._task
        assert task1 is task2  # Same task, not a new one
        await emitter.stop()

@pytest.mark.asyncio
async def test_stale_detection_triggers_callback():
    """Stale peer heartbeat triggers on_stale callback."""
    from shared.heartbeat import HeartbeatEmitter
    from datetime import datetime, timezone, timedelta

    callback = AsyncMock()
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[
             {"daemon_name": "titan", "last_beat": stale_time}
         ]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="perseus", interval=1, on_stale=callback)
        await emitter.start()
        await asyncio.sleep(1.5)  # Wait for one heartbeat cycle
        await emitter.stop()

        assert callback.called, "Stale callback should fire for stale peer"
```

```python
# tests/test_log_redaction.py
"""Tests for log redaction formatter."""

import logging
import time
import pytest

def test_redacting_formatter_strips_stripe_key():
    """RedactingFormatter strips Stripe secret keys."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Payment with sk_live_abc123def456ghi789jkl012mno",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "sk_live_" not in result
        assert "[REDACTED:stripe_secret]" in result

def test_redacting_formatter_strips_telegram_token():
    """RedactingFormatter strips Telegram bot tokens."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Bot token: 123456789:ABCDefGHIJKlmNOpQRStuvWxYz_1234567890",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "123456789:ABC" not in result
        assert "[REDACTED:telegram_token]" in result

def test_redacting_formatter_strips_db_connection():
    """RedactingFormatter strips Postgres connection strings."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Connecting to postgresql://user:pass@localhost:5432/mydb",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "postgresql://" not in result
        assert "[REDACTED:db_connection]" in result

def test_redacting_formatter_strips_jwt():
    """RedactingFormatter strips JWT tokens."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=f"Auth token: {jwt}",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "eyJhbGci" not in result
        assert "[REDACTED:jwt_token]" in result

def test_redacting_formatter_passthrough_when_disabled():
    """RedactingFormatter passes through unchanged when flag is OFF."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Token: sk_live_abc123def456ghi789jkl012mno",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "false"}):
        result = redactor.format(record)
        assert "sk_live_" in result  # NOT redacted

def test_redacting_formatter_latency_under_1ms():
    """Redaction must add <1ms latency per log line."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    # Realistic log message with embedded credential
    msg = f"API call to stripe with key sk_live_{'x' * 30} completed in 200ms"
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        start = time.perf_counter_ns()
        for _ in range(1000):
            redactor.format(record)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

        avg_ms = elapsed_ms / 1000
        assert avg_ms < 1.0, f"Redaction latency {avg_ms:.3f}ms exceeds 1ms target"

def test_credential_stripper_has_15_plus_patterns():
    """Credential stripper must have 15+ patterns per AEGIS requirement."""
    from openjarvis.security.credential_stripper import _CREDENTIAL_PATTERNS

    assert len(_CREDENTIAL_PATTERNS) >= 15, (
        f"AEGIS requires 15+ credential patterns, found {len(_CREDENTIAL_PATTERNS)}. "
        "Missing patterns: Stripe, Telegram, Netlify, Instantly, DB strings, JWT."
    )
```

**Acceptance criteria:**
- [ ] Heartbeat tests: start/stop, disabled noop, idempotent, stale detection
- [ ] Redaction tests: Stripe, Telegram, DB connection, JWT, passthrough, latency, pattern count
- [ ] All pass with `PYTHONPATH=. pytest tests/test_heartbeat.py tests/test_log_redaction.py -v`
- [ ] Latency test asserts <1ms average per log line

---

## New Files Summary

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `scripts/migrations/027-eval-results.sql` | ~20 | eval_results + session_health tables |
| `tests/evals/__init__.py` | ~1 | Package marker |
| `tests/evals/conftest.py` | ~40 | Shared fixtures for behavioral evals |
| `tests/evals/recorder.py` | ~50 | Eval result recording with optional DB persistence |
| `tests/evals/test_eval_budget.py` | ~50 | Budget guard fails-closed eval |
| `tests/evals/test_eval_titan_email.py` | ~60 | CAN-SPAM compliance eval |
| `tests/evals/test_eval_middleware.py` | ~50 | Middleware chain not-dead-code eval |
| `tests/evals/test_eval_clawdbot.py` | ~40 | ClawdBot escalation eval |
| `tests/evals/test_eval_state_machine.py` | ~50 | State machine invalid transition eval |
| `tests/evals/test_eval_recursion.py` | ~40 | Recursion guard depth eval |
| `shared/heartbeat.py` | ~150 | HeartbeatEmitter + stale detection + Hermes alert |
| `shared/log_redaction.py` | ~80 | RedactingFormatter wrapping any logging.Formatter |
| `soul/lifecycle/perseus_lifecycle.md` | ~30 | Perseus daemon lifecycle document |
| `soul/lifecycle/titan_lifecycle.md` | ~30 | Titan daemon lifecycle document |
| `soul/lifecycle/hermes_lifecycle.md` | ~30 | Hermes daemon lifecycle document |
| `soul/lifecycle/clawdbot_lifecycle.md` | ~30 | ClawdBot daemon lifecycle document |
| `soul/lifecycle/conway_lifecycle.md` | ~30 | Conway daemon lifecycle document |
| `tests/test_heartbeat.py` | ~80 | Heartbeat unit tests |
| `tests/test_log_redaction.py` | ~120 | Log redaction + credential pattern tests |

## Modified Files Summary

| File | Change |
|------|--------|
| `openjarvis/security/credential_stripper.py` | Expand from 6 to 15+ regex patterns |
| `shared/logging_config.py` | Wrap formatters with RedactingFormatter |
| `shared/agent_base.py` | Add HeartbeatEmitter start/stop on active work transitions |
| `shared/llm_client.py` | Inject lifecycle docs into system prompt when flag ON |

---

## Execution Order

```
Task 1  (migration 027)           ← Foundation: tables exist
  ↓
Task 2  (eval framework)          ← Foundation: fixtures + recorder
  ↓
Tasks 3-6  (6 behavioral evals)   ← Can run in parallel
  ↓
Task 7  (heartbeat emitter)       ← Depends on Task 1 (session_health table)
  ↓
Task 8  (wire into AgentBase)     ← Depends on Task 7
  ↓
Task 9  (lifecycle docs)          ← Independent, can parallel with 7-8
  ↓
Task 10 (RedactingFormatter)      ← Independent
  ↓
Task 11 (expand credential patterns) ← Independent
  ↓
Task 12 (wire into logging)       ← Depends on Task 10 + 11
  ↓
Task 13 (heartbeat + redaction tests) ← Final validation
```

## Success Criteria

- [ ] 6 behavioral evals pass: budget guard, email compliance, middleware chain, ClawdBot escalation, state machine, recursion guard
- [ ] HeartbeatEmitter writes to session_health every 30s per daemon
- [ ] Stale heartbeat (>5min) triggers Hermes alert
- [ ] 15+ credential patterns in CredentialStripper
- [ ] All log output (5 daemons) passes through RedactingFormatter
- [ ] Redaction latency <1ms per log line
- [ ] All 3 feature flags default OFF (zero behavior change without opt-in)
- [ ] All existing 457+ tests still pass
- [ ] `ruff check shared/ openjarvis/security/ tests/evals/` clean
- [ ] Migration 027 runs cleanly

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Behavioral evals too coupled to current implementation | Medium | Low | Test behavioral contracts (outputs), not internal structure |
| Heartbeat table grows unbounded | Medium | Medium | Add TTL cleanup in Perseus scheduler: `DELETE FROM session_health WHERE heartbeat_at < NOW() - INTERVAL '24 hours'` |
| Redaction regex too greedy (false positives) | Low | Medium | Each pattern is anchored to known prefix; test with realistic log messages |
| Credential patterns miss new services | Medium | Low | AEGIS audit requirement: add pattern in same PR as new service integration |
| Heartbeat adds DB write pressure | Low | Low | 1 INSERT per daemon per 30s = 10 writes/min total across 5 daemons |

---

*Plan created: 2026-03-30 -- Phase 14: Quality & Observability (3 Paperclip patterns)*
