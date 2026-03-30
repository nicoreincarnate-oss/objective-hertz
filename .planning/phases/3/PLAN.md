# Phase 3: DeerFlow Persistent Memory

**Goal:** Daemons remember context across restarts. Three-tier memory with garbage collection.
**Requirements:** MEM-01, MEM-02, MEM-03, MEM-04, MEM-05, MEM-06, MEM-07, MEM-08
**Depends on:** Phase 0b (contracts), Phase 1 (DNA for memory isolation)
**Feature flag:** `ENABLE_DEERFLOW_MEMORY`

---

## Context

DeerFlow's persistent memory pattern gives daemons continuity across restarts. Three memory tiers: working (in-process dict, lost on restart), episodic (30-day expiry, decisions and events), and semantic (permanent, compressed knowledge). MAGMA (`shared/magma.py`) already provides graph-augmented retrieval — DeerFlow memory operates alongside it as a simpler key-value store optimized for daemon-local context.

`AgentBase` (`shared/agent_base.py`) has `register()`/`deregister()` lifecycle hooks but NO memory hooks yet. Memory injection needs `_load_memory()` at startup and `_save_memory()` at shutdown.

### Current State

| Component | File | Status |
|-----------|------|--------|
| AgentBase lifecycle | `shared/agent_base.py:28-159` | Has register/deregister, NO memory hooks |
| MAGMA | `shared/magma.py` | Full graph memory — separate from DeerFlow |
| daemon_memory table | None | Does not exist |
| Memory store | `shared/contracts.py` | `MemoryStore` Protocol defined in Phase 0b |
| Latest migration | `scripts/migrations/016-hosting-deploy-url.sql` | Next = 017 |

---

## Tasks

### Task 1: daemon_memory table (MEM-01)
**File:** `scripts/migrations/019-daemon-memory.sql` (new)
**What:**

```sql
CREATE TABLE IF NOT EXISTS daemon_memory (
    id SERIAL PRIMARY KEY,
    daemon_name TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (memory_type IN ('episodic', 'semantic')),
    key TEXT NOT NULL,
    content JSONB NOT NULL,
    importance DECIMAL(3,2) DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    expires_at TIMESTAMPTZ,  -- NULL for semantic (permanent)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (daemon_name, memory_type, key)
);

-- Per-daemon row cap enforced at application level (10K per daemon)
CREATE INDEX idx_daemon_memory_daemon ON daemon_memory (daemon_name, memory_type);
CREATE INDEX idx_daemon_memory_expires ON daemon_memory (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_daemon_memory_importance ON daemon_memory (daemon_name, importance DESC);

-- Monitoring view (MEM-08)
CREATE VIEW daemon_memory_stats AS
SELECT
    daemon_name,
    memory_type,
    COUNT(*) as row_count,
    AVG(importance) as avg_importance,
    MIN(created_at) as oldest_entry,
    MAX(updated_at) as newest_entry,
    SUM(CASE WHEN expires_at < NOW() THEN 1 ELSE 0 END) as expired_count
FROM daemon_memory
GROUP BY daemon_name, memory_type;
```

**Acceptance criteria:**
- [ ] `daemon_memory` table created with all columns
- [ ] Unique constraint on `(daemon_name, memory_type, key)`
- [ ] `daemon_memory_stats` view works
- [ ] Migration runs cleanly

### Task 2: Working memory — in-process only (MEM-02)
**File:** `shared/daemon_memory.py` (new)
**What:**

```python
class WorkingMemory:
    """In-process working memory. Lost on restart. NOT in Postgres."""

    def __init__(self, max_items: int = 100):
        self._store: dict[str, Any] = {}
        self._max = max_items

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max:
            # Evict oldest
            oldest = next(iter(self._store))
            del self._store[oldest]
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()
```

**Acceptance criteria:**
- [ ] Working memory is a Python dict, NOT Postgres
- [ ] Max items enforced with LRU-like eviction
- [ ] Lost on process restart (by design)

### Task 3: Episodic memory with daily cleanup (MEM-03)
**File:** `shared/daemon_memory.py` (extend)
**What:**

```python
class DaemonMemoryStore:
    """Persistent daemon memory. Implements MemoryStore Protocol."""

    async def save_episodic(self, daemon_name: str, key: str, content: dict,
                            importance: float = 0.5, ttl_days: int = 30) -> None:
        """Save episodic memory with expiry."""
        expires_at = datetime.utcnow() + timedelta(days=ttl_days)
        await execute(
            """INSERT INTO daemon_memory (daemon_name, memory_type, key, content, importance, expires_at)
               VALUES (%s, 'episodic', %s, %s, %s, %s)
               ON CONFLICT (daemon_name, memory_type, key) DO UPDATE
               SET content = EXCLUDED.content, importance = EXCLUDED.importance,
                   expires_at = EXCLUDED.expires_at, updated_at = NOW()""",
            (daemon_name, key, Jsonb(content), importance, expires_at),
        )

    async def cleanup_expired(self) -> int:
        """Delete expired episodic memories. Run daily via Perseus scheduler."""
        result = await execute(
            "DELETE FROM daemon_memory WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        return result  # rows deleted
```

Daily cleanup job added to `perseus/scheduler.py` as a new scheduled task.

**Acceptance criteria:**
- [ ] Episodic memories have 30-day default expiry
- [ ] Cleanup deletes expired rows
- [ ] Perseus scheduler runs cleanup daily

### Task 4: Semantic memory with MAGMA compression (MEM-04)
**File:** `shared/daemon_memory.py` (extend)
**What:**

```python
async def save_semantic(self, daemon_name: str, key: str, content: dict,
                        importance: float = 0.7) -> None:
    """Save semantic memory (permanent, no expiry)."""
    # Check row cap
    count = await self._get_row_count(daemon_name)
    if count >= 10000:
        # Compress: merge lowest-importance entries via MAGMA
        await self._compress_memories(daemon_name)

    await execute(
        """INSERT INTO daemon_memory (daemon_name, memory_type, key, content, importance)
           VALUES (%s, 'semantic', %s, %s, %s)
           ON CONFLICT (daemon_name, memory_type, key) DO UPDATE
           SET content = EXCLUDED.content, importance = EXCLUDED.importance, updated_at = NOW()""",
        (daemon_name, key, Jsonb(content), importance),
    )

async def _compress_memories(self, daemon_name: str) -> None:
    """When row cap exceeded, merge low-importance memories."""
    # Get bottom 20% by importance
    # Use MAGMA's semantic merge to combine similar entries
    # Delete originals, insert compressed versions
```

**Acceptance criteria:**
- [ ] Semantic memories persist indefinitely (no `expires_at`)
- [ ] Row cap (10K per daemon) triggers compression
- [ ] MAGMA compression merges similar low-importance entries

### Task 5: JSON cache write-through (MEM-05)
**File:** `shared/daemon_memory.py` (extend)
**What:**

```python
class MemoryCache:
    """JSON file cache for fast daemon startup. Write-through to Postgres."""

    CACHE_DIR = Path.home() / ".objective-hertz" / "memory-cache"

    async def load_cache(self, daemon_name: str) -> dict:
        """Load cached memories from JSON file. Fast startup path."""
        cache_file = self.CACHE_DIR / f"{daemon_name}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        return {}

    async def save_cache(self, daemon_name: str, memories: dict) -> None:
        """Write memories to JSON cache file."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = self.CACHE_DIR / f"{daemon_name}.json"
        cache_file.write_text(json.dumps(memories, default=str))

    async def save_with_cache(self, daemon_name: str, key: str, content: dict, **kwargs):
        """Write to Postgres AND update JSON cache."""
        await self._store.save_episodic(daemon_name, key, content, **kwargs)
        # Update cache in background
        cache = await self.load_cache(daemon_name)
        cache[key] = content
        await self.save_cache(daemon_name, cache)
```

Startup flow: Load JSON cache first (< 500ms), then async-sync with Postgres in background.

**Acceptance criteria:**
- [ ] JSON cache startup time < 500ms per daemon
- [ ] Write-through: every DB write also updates cache
- [ ] Cache invalidated when Postgres is authoritative (periodic sync)

### Task 6: Memory injection into daemon startup (MEM-06)
**File:** `shared/agent_base.py` (modify)
**What:**

Add lifecycle hooks to `AgentBase`:

```python
class AgentBase(ABC):
    def __init__(self):
        # ... existing init ...
        self._memory: DaemonMemoryStore | None = None
        self._working_memory: WorkingMemory = WorkingMemory()

    async def _load_memory(self) -> None:
        """Load persistent memories at startup. Called during register()."""
        if not feature_flag("ENABLE_DEERFLOW_MEMORY"):
            return
        self._memory = DaemonMemoryStore()
        cached = await MemoryCache().load_cache(self.name)
        # Inject into working memory for immediate use
        for key, value in cached.items():
            self._working_memory.set(key, value)

    async def _save_memory(self) -> None:
        """Persist working memory state at shutdown. Called during deregister()."""
        if not self._memory:
            return
        # Save important working memory to episodic
        for key, value in self._working_memory._store.items():
            await self._memory.save_episodic(self.name, key, value)

    async def register(self):
        # ... existing code ...
        await self._load_memory()  # ADD THIS

    async def deregister(self):
        await self._save_memory()  # ADD THIS
        # ... existing code ...
```

**Acceptance criteria:**
- [ ] `_load_memory()` called during `register()`
- [ ] `_save_memory()` called during `deregister()`
- [ ] Feature flag gates all memory operations
- [ ] Daemon restart preserves recent decisions and context

### Task 7: Per-daemon memory isolation (MEM-07)
**File:** `shared/daemon_memory.py` (extend)
**What:**

Memory isolation enforced by DNA profiles (`memory_domains` field from Phase 1):

```python
async def load(self, daemon_name: str, memory_type: str) -> list[dict]:
    """Load memories. Enforces domain isolation."""
    # Check DNA profile for allowed memory_domains
    allowed_domains = self._get_allowed_domains(daemon_name)
    if daemon_name not in allowed_domains:
        raise PermissionError(f"{daemon_name} cannot access these memories")

    return await fetch_all(
        "SELECT key, content FROM daemon_memory WHERE daemon_name = %s AND memory_type = %s",
        (daemon_name, memory_type),
    )
```

- Titan cannot read ClawdBot memories (and vice versa)
- Each daemon's DNA profile specifies its `memory_domains`
- Cross-daemon queries raise `PermissionError`

**Acceptance criteria:**
- [ ] Cross-daemon memory isolation enforced
- [ ] Titan cannot read ClawdBot memories
- [ ] Isolation configured via DNA profile `memory_domains` field

### Task 8: Tests
**Files:**
- `tests/shared/test_daemon_memory.py` (new)
- `tests/shared/test_memory_cache.py` (new)

**What:**
- Test episodic save/load/expire cycle
- Test semantic save with compression at cap
- Test working memory eviction
- Test JSON cache round-trip and startup time (< 500ms)
- Test memory isolation (cross-daemon access blocked)
- Test `isinstance(DaemonMemoryStore(), MemoryStore)` passes
- Test `daemon_memory_stats` view returns correct counts

**Acceptance criteria:**
- [ ] All tests pass with `PYTHONPATH=. pytest tests/shared/test_daemon_memory.py tests/shared/test_memory_cache.py -v`
- [ ] `ruff check shared/daemon_memory.py` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] Daemon restart preserves recent decisions and context
- [ ] Cross-daemon memory isolation: Titan cannot read ClawdBot memories
- [ ] 30-day expiry cleanup runs daily without errors
- [ ] JSON cache startup time < 500ms per daemon
- [ ] `daemon_memory_stats` view shows per-daemon row counts

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| JSON cache diverges from Postgres | Medium | Medium | Periodic sync job reconciles; Postgres is authoritative |
| MAGMA compression loses important memories | Low | High | Only compress bottom 20% by importance; keep high-importance intact |
| 10K row cap too low for active daemons | Low | Low | Configurable per daemon; monitor via stats view |

---

*Plan created: 2026-03-29*
