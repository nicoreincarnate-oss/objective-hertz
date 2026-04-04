# Phase 12: Foundation Patterns — PLAN

**Goal:** Port 5 infrastructure patterns from Paperclip (MIT) into Objective Hertz — agent state machine, atomic task checkout, recursion guard, forbidden token scanner, and session health — hardening the daemon runtime for production autonomy.
**Requirements:** FP-01 through FP-05
**Depends on:** Phase 11 (Budget Consolidation) must be complete.
**Feature flags:** All default OFF. Enable individually as each pattern passes tests.
**License:** MIT (Paperclip source)

---

## Context

### Current State

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| AgentBase | `shared/agent_base.py` | 370 | start/stop/health_check, claim_task, complete_task, fail_task |
| DB helpers | `shared/db.py` | 235 | Pool, insert_task, emit_event, get/set_config, transaction() |
| Middleware chain | `shared/middleware.py` | 452 | 6 middlewares: budget_check, dna_guard, anti_slop, neuro_scorer, memory, telemetry |
| Agent registry | `perseus/agent_registry.py` | 48 | get_active_agents, heartbeat, check_agent_health |
| WorkingMemory | `shared/daemon_memory.py` | OrderedDict-based, max_items LRU, get/set/delete/clear/items |
| Credential stripper | `openjarvis/security/credential_stripper.py` | 31 | 6 regex patterns, strip() method |
| Loop guard | `openjarvis/agents/loop_guard.py` | Existing recursion protection |
| Observability | `shared/observability.py` | 538 | Tracing, Prometheus, record_llm_call |
| task_queue table | `scripts/init-db.sql` | id, task_type, payload, status, priority, assigned_agent, retry_count, created_at, started_at, completed_at, error, result |
| agent_registry table | `scripts/init-db.sql` | name (PK), description, status, last_heartbeat, updated_at |
| Latest migration | `scripts/migrations/024-neuro-scores.sql` | -- |

---

## Feature Flags

| Flag | Env Var | Default | Pattern |
|------|---------|---------|---------|
| Agent State Machine | `AGENT_STATE_MACHINE_ENABLED` | `false` | 1 |
| Atomic Checkout | `ATOMIC_CHECKOUT_ENABLED` | `false` | 2 |
| Recursion Guard | `RECURSION_GUARD_ENABLED` | `false` | 3 |
| Forbidden Token Scanner | `FORBIDDEN_TOKEN_SCAN_ENABLED` | `false` | 4 |
| Session Health | `SESSION_HEALTH_ENABLED` | `false` | 5 |

All flags checked via `os.environ.get(flag, "").lower() in ("true", "1")` — consistent with existing `_deerflow_memory_enabled()` pattern in `shared/agent_base.py` line 36.

---

## Tasks

### Plan 12-01: Agent State Machine (FP-01)

#### Task 1A: State and Pause enums
**File:** `shared/agent_state.py` (new, ~80 lines)
**Action:** Create module with enums and transition matrix.

```python
from enum import Enum

class AgentState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"

class PauseReason(str, Enum):
    MANUAL = "manual"
    BUDGET = "budget"
    SYSTEM = "system"
    ERROR_THRESHOLD = "error_threshold"

# Valid transitions: from_state -> set of allowed to_states
TRANSITION_MATRIX: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE:       {AgentState.PLANNING, AgentState.EXECUTING, AgentState.PAUSED, AgentState.TERMINATED},
    AgentState.PLANNING:   {AgentState.EXECUTING, AgentState.IDLE, AgentState.PAUSED, AgentState.ERROR},
    AgentState.EXECUTING:  {AgentState.REVIEWING, AgentState.IDLE, AgentState.PAUSED, AgentState.ERROR},
    AgentState.REVIEWING:  {AgentState.IDLE, AgentState.EXECUTING, AgentState.PAUSED, AgentState.ERROR},
    AgentState.PAUSED:     {AgentState.IDLE, AgentState.TERMINATED},
    AgentState.ERROR:      {AgentState.IDLE, AgentState.PAUSED, AgentState.TERMINATED},
    AgentState.TERMINATED: set(),  # terminal state — no transitions out
}

def validate_transition(from_state: AgentState, to_state: AgentState) -> bool:
    """Return True if transition is allowed by the matrix."""
    return to_state in TRANSITION_MATRIX.get(from_state, set())
```

#### Task 1B: Wire into AgentBase
**File:** `shared/agent_base.py`
**Action:** Add `_state`, `_pause_reason` attributes and `_transition()` method.

**Details:**
1. Import `AgentState`, `PauseReason`, `validate_transition` from `shared.agent_state`
2. In `__init__()` (after line 57), add:
   ```python
   self._state: AgentState = AgentState.IDLE
   self._pause_reason: PauseReason | None = None
   ```
3. Add `_agent_state_enabled()` static helper (same pattern as `_deerflow_memory_enabled()`):
   ```python
   def _agent_state_enabled() -> bool:
       return os.environ.get("AGENT_STATE_MACHINE_ENABLED", "").lower() in ("true", "1")
   ```
4. Add `_transition(self, new_state: AgentState, pause_reason: PauseReason | None = None) -> bool` method:
   - If `not _agent_state_enabled()`: return True (no-op passthrough)
   - Call `validate_transition(self._state, new_state)` — if False, log warning and return False
   - Set `self._state = new_state`
   - Set `self._pause_reason = pause_reason` (only when `new_state == AgentState.PAUSED`, else None)
   - Persist to DB: `await db.execute("UPDATE agent_registry SET state = %s, pause_reason = %s WHERE name = %s", (new_state.value, pause_reason.value if pause_reason else None, self.name))`
   - Emit event: `await self.emit_event("agent_state_change", {"from": old_state.value, "to": new_state.value, "pause_reason": pause_reason.value if pause_reason else None})`
   - Return True
5. Add read-only properties:
   ```python
   @property
   def state(self) -> AgentState:
       return self._state

   @property
   def pause_reason(self) -> PauseReason | None:
       return self._pause_reason
   ```

#### Task 1C: Update agent_registry queries
**File:** `perseus/agent_registry.py`
**Action:** Include `state` and `pause_reason` in `check_agent_health()` return dict.

**Details:**
- In `check_agent_health()` (line 35), update the SELECT to include `state, pause_reason`:
  ```sql
  SELECT name, status, state, pause_reason, last_heartbeat,
         EXTRACT(EPOCH FROM NOW() - last_heartbeat) as seconds_since_heartbeat
  FROM agent_registry
  ```
- Add `state` and `pause_reason` to the returned health dict for each agent (lines 43-47)

#### Task 1D: Integrate state transitions into lifecycle
**File:** `shared/agent_base.py`
**Action:** Call `_transition()` at lifecycle boundaries.

**Details:**
- `register()` (line 161): after DB insert, call `await self._transition(AgentState.IDLE)`
- `claim_task()` (line 320): on successful claim, call `await self._transition(AgentState.EXECUTING)`
- `complete_task()` (line 334): call `await self._transition(AgentState.IDLE)`
- `fail_task()` (line 344): call `await self._transition(AgentState.ERROR)`
- `request_shutdown()` (line 238): call `self._state = AgentState.TERMINATED` (sync, no DB write needed since deregister handles cleanup)
- `finalize_shutdown()` (line 274): call `await self._transition(AgentState.TERMINATED)` before deregister

---

### Plan 12-02: Atomic Task Checkout (FP-02)

#### Task 2A: Add atomic checkout to get_pending_tasks()
**File:** `shared/agent_base.py`
**Action:** Replace SELECT in `get_pending_tasks()` with `FOR UPDATE SKIP LOCKED` when flag is on.

**Details:**
1. Add flag helper at module level:
   ```python
   def _atomic_checkout_enabled() -> bool:
       return os.environ.get("ATOMIC_CHECKOUT_ENABLED", "").lower() in ("true", "1")
   ```
2. Modify `get_pending_tasks()` (line 304). When `_atomic_checkout_enabled()` is True, the caller must use `db.transaction()` context and the query appends `FOR UPDATE SKIP LOCKED`. When False, behavior is identical to current code.

**New implementation:**
```python
async def get_pending_tasks(self, task_type: str | None = None) -> list[dict]:
    """Get pending tasks. Uses FOR UPDATE SKIP LOCKED when atomic checkout is enabled."""
    lock_clause = "FOR UPDATE SKIP LOCKED" if _atomic_checkout_enabled() else ""
    base_where = "WHERE (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))"

    if task_type:
        query = f"""SELECT * FROM task_queue
                    {base_where} AND task_type = %s
                    ORDER BY priority ASC, created_at ASC LIMIT 50
                    {lock_clause}"""
        return await db.fetch_all(query, (task_type,))

    query = f"""SELECT * FROM task_queue
                {base_where}
                ORDER BY priority ASC, created_at ASC LIMIT 50
                {lock_clause}"""
    return await db.fetch_all(query)
```

#### Task 2B: Wrap claim_task in SAVEPOINT transaction
**File:** `shared/agent_base.py`
**Action:** Modify `claim_task()` to use explicit transaction with SAVEPOINT when atomic checkout is enabled.

**Details:**
```python
async def claim_task(self, task_id: int) -> bool:
    """Claim a task atomically. Uses SAVEPOINT when atomic checkout is enabled."""
    if _atomic_checkout_enabled():
        async with db.transaction() as conn:
            await conn.execute("SAVEPOINT task_claim")
            try:
                cursor = await conn.execute(
                    """UPDATE task_queue
                       SET status = 'running', started_at = NOW(), assigned_agent = %s
                       WHERE id = %s
                         AND (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))
                       RETURNING id""",
                    (self.name, task_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    await conn.execute("ROLLBACK TO SAVEPOINT task_claim")
                    return False
                await conn.execute("RELEASE SAVEPOINT task_claim")
                record_task_claimed(self.name)
                return True
            except Exception:
                await conn.execute("ROLLBACK TO SAVEPOINT task_claim")
                raise
    else:
        # Existing behavior — unchanged
        row = await db.fetch_one(
            """UPDATE task_queue
               SET status = 'running', started_at = NOW(), assigned_agent = %s
               WHERE id = %s
                 AND (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))
               RETURNING id""",
            (self.name, task_id),
        )
        if row is not None:
            record_task_claimed(self.name)
        return row is not None
```

---

### Plan 12-03: Recursion Guard (FP-03)

#### Task 3A: Add depth parameter to insert_task()
**File:** `shared/db.py`
**Action:** Add optional `depth: int = 0` parameter to `insert_task()`.

**Details:**
- Modify `insert_task()` signature (line 155) to accept `depth: int = 0`
- Modify the INSERT query (line 180) to include the depth column:
  ```python
  row = await fetch_one(
      """INSERT INTO task_queue (task_type, payload, priority, depth)
         VALUES (%s, %s, %s, %s) RETURNING id""",
      (task_type, json.dumps(payload or {}), priority, depth),
  )
  ```

#### Task 3B: Add depth check to claim_task()
**File:** `shared/agent_base.py`
**Action:** Reject tasks exceeding max depth when recursion guard is enabled.

**Details:**
1. Add flag helper:
   ```python
   def _recursion_guard_enabled() -> bool:
       return os.environ.get("RECURSION_GUARD_ENABLED", "").lower() in ("true", "1")
   ```
2. In `claim_task()`, BEFORE the UPDATE, check depth:
   ```python
   if _recursion_guard_enabled():
       max_depth = await db.get_config("max_task_depth", 5)
       depth_row = await db.fetch_one(
           "SELECT depth FROM task_queue WHERE id = %s", (task_id,)
       )
       if depth_row and (depth_row.get("depth") or 0) > max_depth:
           self.logger.warning(
               "Recursion guard: task %d rejected (depth=%d, max=%d)",
               task_id, depth_row["depth"], max_depth,
           )
           await self.fail_task(task_id, f"recursion_guard: depth {depth_row['depth']} > max {max_depth}")
           return False
   ```

#### Task 3C: Propagate depth when spawning child tasks
**File:** `shared/agent_base.py`
**Action:** Add `spawn_child_task()` convenience method.

**Details:**
```python
async def spawn_child_task(
    self,
    parent_task_id: int,
    task_type: str,
    payload: dict | None = None,
    priority: int = 5,
) -> int | None:
    """Insert a child task with depth = parent.depth + 1."""
    parent = await db.fetch_one(
        "SELECT depth FROM task_queue WHERE id = %s", (parent_task_id,)
    )
    parent_depth = (parent.get("depth") or 0) if parent else 0
    return await db.insert_task(
        task_type=task_type,
        payload=payload,
        priority=priority,
        depth=parent_depth + 1,
        dedupe=False,
    )
```

---

### Plan 12-04: Forbidden Token Scanner (FP-04)

#### Task 4A: Create scanner module
**File:** `openjarvis/security/forbidden_tokens.py` (new, ~120 lines)
**Action:** Regex-based scanner that detects leaked secrets, OS usernames, injection markers.

**Details:**
```python
"""Forbidden token scanner — detects secrets and PII leaking into LLM output.

Source: Paperclip check-forbidden-tokens.mjs (MIT), adapted for Python.
Feature flag: FORBIDDEN_TOKEN_SCAN_ENABLED
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger("openjarvis.security.forbidden_tokens")

class TokenMatch(NamedTuple):
    pattern_name: str
    matched_text: str
    position: int

# OS username detection
_OS_USERNAME = os.environ.get("USER", os.environ.get("USERNAME", ""))

# Core patterns (compiled once at import)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # API key formats
    ("openai_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}")),
    ("stripe_secret", re.compile(r"sk_(?:live|test)_[a-zA-Z0-9]{20,}")),
    ("stripe_publishable", re.compile(r"pk_(?:live|test)_[a-zA-Z0-9]{20,}")),
    ("stripe_restricted", re.compile(r"rk_live_[a-zA-Z0-9]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[ps]_[a-zA-Z0-9]{36}")),
    ("netlify_token", re.compile(r"nfp_[a-zA-Z0-9]{40,}")),
    ("telegram_bot_token", re.compile(r"\d{8,10}:[a-zA-Z0-9_-]{35}")),
    ("instantly_key", re.compile(r"inst_[a-zA-Z0-9]{20,}")),
    ("jwt_token", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+")),
    # Database connection strings
    ("postgres_dsn", re.compile(r"postgresql?://[^:]+:[^@]+@[^\s]+")),
    # .env value patterns (KEY=value on same line)
    ("env_leak", re.compile(r"(?:API_KEY|SECRET|PASSWORD|TOKEN|DSN)\s*=\s*\S{8,}")),
    # Common injection markers
    ("injection_marker", re.compile(r"(?:<\|system\|>|<\|user\|>|<\|assistant\|>|\[INST\]|\[/INST\])")),
]


def _load_allowlist(project_root: Path | None = None) -> set[str]:
    """Load patterns to ignore from .forbidden-tokens-allow."""
    allow_path = (project_root or Path.cwd()) / ".forbidden-tokens-allow"
    if not allow_path.exists():
        return set()
    lines = allow_path.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def scan(text: str, *, include_username: bool = True, project_root: Path | None = None) -> list[TokenMatch]:
    """Scan text for forbidden tokens. Returns list of matches."""
    allowlist = _load_allowlist(project_root)
    matches: list[TokenMatch] = []

    for name, pattern in _PATTERNS:
        if name in allowlist:
            continue
        for m in pattern.finditer(text):
            matches.append(TokenMatch(name, m.group()[:40] + "...", m.start()))

    # OS username check (only if username is 3+ chars to avoid false positives)
    if include_username and _OS_USERNAME and len(_OS_USERNAME) >= 3:
        if "os_username" not in allowlist:
            for m in re.finditer(re.escape(_OS_USERNAME), text, re.IGNORECASE):
                matches.append(TokenMatch("os_username", _OS_USERNAME, m.start()))

    return matches


def scan_or_raise(text: str, **kwargs) -> str:
    """Scan and raise ValueError if any forbidden tokens found. Returns text unchanged if clean."""
    hits = scan(text, **kwargs)
    if hits:
        names = ", ".join(sorted({h.pattern_name for h in hits}))
        raise ValueError(f"Forbidden tokens detected: {names} ({len(hits)} match(es))")
    return text
```

#### Task 4B: Wire as middleware (position 7)
**File:** `shared/middleware.py`
**Action:** Add `forbidden_token_middleware` and register at position 7 (after telemetry, as output sanitizer).

**Details:**
1. Add middleware function after `telemetry_middleware`:
   ```python
   async def forbidden_token_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
       """Scan stage output for leaked secrets and forbidden tokens.

       Feature-flag gated by FORBIDDEN_TOKEN_SCAN_ENABLED.
       Runs AFTER the stage (output sanitizer) — checks result["output"].
       """
       if not _flag("FORBIDDEN_TOKEN_SCAN_ENABLED"):
           return await next_fn(ctx)

       result = await next_fn(ctx)

       output_text = str(result.get("output", ""))
       if not output_text:
           return result

       try:
           from openjarvis.security.forbidden_tokens import scan
           hits = scan(output_text)
           if hits:
               names = ", ".join(sorted({h.pattern_name for h in hits}))
               logger.error(
                   "FORBIDDEN TOKENS in stage '%s': %s (%d matches)",
                   ctx.get("stage_name", "unknown"), names, len(hits),
               )
               result["forbidden_token_violations"] = [
                   {"pattern": h.pattern_name, "position": h.position} for h in hits
               ]
               # Redact the output using credential stripper as fallback
               from openjarvis.security.credential_stripper import CredentialStripper
               result["output"] = CredentialStripper().strip(output_text)
       except Exception as exc:
           logger.warning("Forbidden token scan failed (non-fatal): %s", exc)

       return result
   ```
2. Add to `MIDDLEWARE_REGISTRY` (line 422):
   ```python
   "forbidden_tokens": forbidden_token_middleware,
   ```
3. Update `TITAN_MIDDLEWARE` list (line 81) to include `"forbidden_tokens"` after `"telemetry"`:
   ```python
   TITAN_MIDDLEWARE = [
       "budget_check",
       "dna_guard",
       "anti_slop",
       "neuro_scorer",
       "memory",
       "telemetry",
       "forbidden_tokens",
   ]
   ```
4. Add `"forbidden_tokens"` to `clawdbot` and `hermes` pipeline configs similarly.
5. Add to `__all__` list.

#### Task 4C: Create allowlist file
**File:** `.forbidden-tokens-allow` (new)
**Action:** Create with test exclusion defaults.

**Details:**
```
# Forbidden token scanner — allowlist
# Lines here suppress specific pattern names in scan results.
# Use for test fixtures and known-safe values.
#
# Format: one pattern_name per line (e.g., "os_username")
```

#### Task 4D: Wire as make quality check
**File:** `Makefile` (or `justfile`)
**Action:** Add `forbidden-tokens` target that runs scanner against staged output.

**Details:**
Add a target that runs:
```bash
python -c "
from openjarvis.security.forbidden_tokens import scan
import sys, pathlib
for f in pathlib.Path('templates').rglob('*.html'):
    hits = scan(f.read_text(), include_username=True)
    if hits:
        print(f'FAIL: {f} — {[h.pattern_name for h in hits]}')
        sys.exit(1)
print('OK: No forbidden tokens found')
"
```

---

### Plan 12-05: Session Health (FP-05)

#### Task 5A: Extend WorkingMemory with health tracking fields
**File:** `shared/daemon_memory.py`
**Action:** Add session health fields to `WorkingMemory` class.

**Details:**
Add these attributes to `WorkingMemory.__init__()` (after line 54):
```python
# Session health tracking (Phase 12: FP-05)
self.total_tokens: int = 0
self.elapsed_seconds: float = 0.0
self.error_count: int = 0
self.state_transitions: int = 0
self.context_saturation_pct: float = 0.0
self._session_start: float = time.time()
self._max_tokens: int = 2_000_000  # 2M token ceiling
self._max_elapsed: float = 72 * 3600  # 72 hours in seconds
```

Add `import time` at top of file.

Add methods:
```python
def record_tokens(self, count: int) -> None:
    """Accumulate token usage."""
    self.total_tokens += count
    self.context_saturation_pct = (self.total_tokens / self._max_tokens) * 100

def record_error(self) -> None:
    """Increment error counter."""
    self.error_count += 1

def record_state_transition(self) -> None:
    """Increment state transition counter."""
    self.state_transitions += 1

def update_elapsed(self) -> None:
    """Update elapsed seconds from session start."""
    self.elapsed_seconds = time.time() - self._session_start

def needs_reset(self) -> bool:
    """Check if session should auto-reset (2M tokens or 72h)."""
    self.update_elapsed()
    return self.total_tokens >= self._max_tokens or self.elapsed_seconds >= self._max_elapsed

def reset(self) -> None:
    """Reset session health counters and clear working memory."""
    self.total_tokens = 0
    self.elapsed_seconds = 0.0
    self.error_count = 0
    self.state_transitions = 0
    self.context_saturation_pct = 0.0
    self._session_start = time.time()
    self.clear()

def health_snapshot(self) -> dict:
    """Return current session health as dict (for DB persistence and API)."""
    self.update_elapsed()
    return {
        "total_tokens": self.total_tokens,
        "elapsed_seconds": round(self.elapsed_seconds, 1),
        "error_count": self.error_count,
        "state_transitions": self.state_transitions,
        "context_saturation_pct": round(self.context_saturation_pct, 2),
        "needs_reset": self.needs_reset(),
        "items_count": len(self),
    }
```

#### Task 5B: Persist session health to database
**File:** `shared/agent_base.py`
**Action:** Periodically flush session health to `session_health` table.

**Details:**
1. Add flag helper:
   ```python
   def _session_health_enabled() -> bool:
       return os.environ.get("SESSION_HEALTH_ENABLED", "").lower() in ("true", "1")
   ```
2. Add `_flush_session_health(self)` method to `AgentBase`:
   ```python
   async def _flush_session_health(self) -> None:
       """Persist current session health metrics to DB."""
       if not _session_health_enabled() or self._working_memory is None:
           return
       try:
           import uuid
           from psycopg.types.json import Jsonb
           snapshot = self._working_memory.health_snapshot()
           await db.execute(
               """INSERT INTO session_health (session_id, agent_id, metrics)
                  VALUES (%s, %s, %s)""",
               (str(uuid.uuid4()), self.name, Jsonb(snapshot)),
           )
       except Exception as exc:
           self.logger.debug("Session health flush failed: %s", exc)
   ```
3. Call `_flush_session_health()` from `_save_memory()` (line 132, inside the try block before persisting entries).
4. In `claim_task()`, after successful claim, if session health enabled:
   ```python
   if _session_health_enabled() and self._working_memory is not None:
       self._working_memory.record_state_transition()
   ```
5. In `fail_task()`, after recording failure:
   ```python
   if _session_health_enabled() and self._working_memory is not None:
       self._working_memory.record_error()
   ```

#### Task 5C: Auto-reset on threshold breach
**File:** `shared/agent_base.py`
**Action:** Check `needs_reset()` before each task claim.

**Details:**
Add check at the top of `claim_task()`:
```python
if _session_health_enabled() and self._working_memory is not None:
    if self._working_memory.needs_reset():
        self.logger.warning(
            "Session auto-reset triggered for %s (tokens=%d, elapsed=%.0fs)",
            self.name,
            self._working_memory.total_tokens,
            self._working_memory.elapsed_seconds,
        )
        await self._flush_session_health()
        self._working_memory.reset()
        await self.emit_event("session_auto_reset", {
            "reason": "threshold_breach",
            "tokens": self._working_memory.total_tokens,
        })
```

---

## Database Migration

### Migration 025: Foundation Patterns

**File:** `scripts/migrations/025-foundation-patterns.sql`

```sql
-- Migration 025: Foundation Patterns (Phase 12)
-- All changes use DEFAULT values — safe for hot tables with zero downtime.

BEGIN;

-- Pattern 1: Agent State Machine
-- Add state tracking to agent_registry
ALTER TABLE agent_registry
    ADD COLUMN IF NOT EXISTS state VARCHAR(20) DEFAULT 'idle';
ALTER TABLE agent_registry
    ADD COLUMN IF NOT EXISTS pause_reason VARCHAR(20) DEFAULT NULL;

-- Pattern 3: Recursion Guard
-- Add depth tracking to task_queue
ALTER TABLE task_queue
    ADD COLUMN IF NOT EXISTS depth INTEGER DEFAULT 0;

-- Pattern 5: Session Health
-- Persistent session health snapshots
CREATE TABLE IF NOT EXISTS session_health (
    session_id UUID PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for querying by agent and time
CREATE INDEX IF NOT EXISTS idx_session_health_agent_created
    ON session_health (agent_id, created_at DESC);

-- Seed feature flags (all OFF by default)
INSERT INTO system_config (key, value) VALUES
    ('AGENT_STATE_MACHINE_ENABLED', 'false'::jsonb),
    ('ATOMIC_CHECKOUT_ENABLED', 'false'::jsonb),
    ('RECURSION_GUARD_ENABLED', 'false'::jsonb),
    ('FORBIDDEN_TOKEN_SCAN_ENABLED', 'false'::jsonb),
    ('SESSION_HEALTH_ENABLED', 'false'::jsonb),
    ('max_task_depth', '5'::jsonb)
ON CONFLICT (key) DO NOTHING;

COMMIT;
```

**Safety notes:**
- `ADD COLUMN ... DEFAULT` is non-blocking on Postgres 11+ (instant for non-volatile defaults)
- `IF NOT EXISTS` / `ON CONFLICT DO NOTHING` makes migration idempotent
- No table locks, no data rewrite — safe to run on live system
- Pattern 2 (Atomic Checkout) requires no schema changes (uses existing `assigned_agent` + `started_at`)
- Pattern 4 (Forbidden Token Scanner) requires no schema changes (pure application code)

---

## Tests Required

**Target: 25 new tests across 5 test files**

### `tests/test_agent_state.py` (new, ~6 tests)
1. `test_valid_transitions()` — IDLE->PLANNING->EXECUTING->REVIEWING->IDLE all succeed
2. `test_invalid_transition_rejected()` — TERMINATED->IDLE raises/returns False
3. `test_paused_requires_reason()` — transition to PAUSED stores PauseReason
4. `test_error_to_idle_recovery()` — ERROR->IDLE is valid
5. `test_transition_matrix_completeness()` — every AgentState appears as a key in TRANSITION_MATRIX
6. `test_feature_flag_off_passthrough()` — `_transition()` returns True when flag is off regardless of states

### `tests/test_atomic_checkout.py` (new, ~5 tests)
1. `test_skip_locked_prevents_double_claim()` — two agents racing get different tasks
2. `test_savepoint_rollback_on_stale_task()` — claiming already-running task returns False, no side effects
3. `test_feature_flag_off_uses_original_query()` — existing behavior unchanged when flag is off
4. `test_claim_within_transaction()` — successful claim commits properly
5. `test_concurrent_checkout_isolation()` — asyncio.gather with 5 agents, each gets unique task

### `tests/test_recursion_guard.py` (new, ~5 tests)
1. `test_depth_zero_default()` — new tasks inserted with depth=0
2. `test_depth_propagation()` — `spawn_child_task()` sets depth = parent + 1
3. `test_max_depth_rejection()` — task with depth > 5 is rejected and failed
4. `test_configurable_max_depth()` — system_config `max_task_depth` overrides default 5
5. `test_feature_flag_off_skips_check()` — depth check skipped when flag is off

### `tests/test_forbidden_tokens.py` (new, ~5 tests)
1. `test_detects_openai_key()` — `sk-abc123...` flagged as `openai_key`
2. `test_detects_stripe_secret()` — `sk_live_...` flagged as `stripe_secret`
3. `test_detects_os_username()` — current OS username flagged
4. `test_allowlist_suppresses_pattern()` — pattern in `.forbidden-tokens-allow` is skipped
5. `test_scan_or_raise_on_clean_text()` — clean text passes through unchanged
6. `test_middleware_redacts_output()` — forbidden_token_middleware replaces leaked tokens in result

### `tests/test_session_health.py` (new, ~5 tests)
1. `test_token_accumulation()` — `record_tokens(1000)` increments total_tokens and updates saturation
2. `test_needs_reset_at_2m_tokens()` — returns True when total_tokens >= 2,000,000
3. `test_needs_reset_at_72h()` — returns True when elapsed >= 72 hours (mock time)
4. `test_reset_clears_all_counters()` — reset() zeros everything and clears OrderedDict
5. `test_health_snapshot_format()` — snapshot dict contains all expected keys with correct types

### Existing test suite
- All 457 existing tests must continue to pass
- Run: `PYTHONPATH=. python3 -m pytest tests/ -v`
- Run: `ruff check shared/ openjarvis/ perseus/`

---

## Success Criteria

- [ ] All 5 feature flags exist and default to OFF
- [ ] Migration 025 applies cleanly on existing schema (idempotent)
- [ ] Agent state transitions persist to agent_registry.state column
- [ ] Invalid state transitions are rejected with logged warning
- [ ] `FOR UPDATE SKIP LOCKED` prevents double-claim when atomic checkout is ON
- [ ] Tasks with depth > max_task_depth are rejected in claim_task()
- [ ] `spawn_child_task()` increments parent depth by 1
- [ ] Forbidden token scanner detects all 14 pattern categories
- [ ] Scanner allowlist suppresses specified patterns
- [ ] Forbidden token middleware redacts output and logs violations
- [ ] WorkingMemory.needs_reset() triggers at 2M tokens or 72h
- [ ] Session health snapshots persist to session_health table
- [ ] All 457 existing tests pass with all flags OFF
- [ ] 25+ new tests pass with flags ON
- [ ] `ruff check` clean on all modified files
- [ ] No f-string SQL anywhere in new code (parameterized queries only)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `FOR UPDATE SKIP LOCKED` adds latency to task checkout | Medium | Low | Only active behind feature flag; benchmark with 100 concurrent claims before enabling |
| State machine adds overhead to every claim/complete/fail cycle | Low | Low | Single UPDATE per transition; batched into existing queries where possible |
| Forbidden token scanner false positives on legitimate content | Medium | Medium | Allowlist file (`.forbidden-tokens-allow`); username check requires >= 3 chars; patterns are tight regexes |
| Session health `needs_reset()` triggers mid-pipeline | Low | High | Reset only checks at `claim_task()` entry — never interrupts in-flight work; flush snapshot before reset |
| Depth column DEFAULT 0 on large task_queue | Low | Low | Postgres 11+ instant `ADD COLUMN ... DEFAULT` — no table rewrite |
| Race between state transition DB write and next claim | Low | Medium | Atomic checkout wraps claim in transaction; state write is advisory, not blocking |

---

## Execution Order

1. **Migration 025** first (schema must exist before code references new columns)
2. **Plan 12-01** Agent State Machine (foundational — other patterns reference state)
3. **Plan 12-03** Recursion Guard (touches insert_task and claim_task — do before atomic checkout)
4. **Plan 12-02** Atomic Checkout (modifies claim_task — apply after recursion guard changes)
5. **Plan 12-04** Forbidden Token Scanner (independent module + middleware wire)
6. **Plan 12-05** Session Health (extends WorkingMemory — last since it touches the most files)
7. **Test suite** — run full suite after each plan, final regression with all flags ON
