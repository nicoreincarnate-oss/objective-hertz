---
phase: 3
plan: 03-01
subsystem: shared/daemon_memory
tags: [deerflow, memory, persistence, isolation]
dependency_graph:
  requires: [shared/contracts.py (MemoryStore Protocol), shared/db.py (async Postgres), soul/dna/*.yaml (memory_domains)]
  provides: [shared/daemon_memory.py (WorkingMemory, DaemonMemoryStore, MemoryCache, IsolatedMemoryStore)]
  affects: [shared/agent_base.py (future lifecycle hooks in 03-02)]
tech_stack:
  added: [psycopg.types.json.Jsonb, yaml.safe_load]
  patterns: [write-through cache, LRU eviction, protocol compliance, DNA-based isolation]
key_files:
  created:
    - scripts/migrations/019-daemon-memory.sql
    - shared/daemon_memory.py
  modified: []
decisions:
  - WorkingMemory uses OrderedDict for true LRU eviction (not plain dict)
  - DaemonMemoryStore.cleanup returns deleted count via RETURNING id (not rowcount)
  - MAGMA compression falls back to simple JSON merge when import fails
  - Memory isolation caches DNA domains in-process for performance
  - IsolatedMemoryStore is a separate wrapper class (not mixed into DaemonMemoryStore)
metrics:
  duration: 3min
  completed: 2026-03-29
  tasks: 5
  files: 2
---

# Phase 3 Plan 01: Memory Store + Cache + Migration Summary

Three-tier DeerFlow persistent memory: daemon_memory Postgres table with stats view, WorkingMemory (in-process LRU dict), DaemonMemoryStore (episodic 30-day TTL + semantic with MAGMA compression at 10K cap), JSON write-through cache for sub-millisecond startup, and per-daemon isolation enforced via DNA profile memory_domains.

## Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Migration 019 — daemon_memory table | 5ea9535 | scripts/migrations/019-daemon-memory.sql |
| 2 | WorkingMemory in-process store | b9849f1 | shared/daemon_memory.py |
| 3 | DaemonMemoryStore episodic/semantic | 01c26ea | shared/daemon_memory.py |
| 4 | JSON write-through memory cache | 3701847 | shared/daemon_memory.py |
| 5 | Per-daemon memory isolation | 2a2bfa9 | shared/daemon_memory.py |

## What Was Built

### Migration 019 (Task 1)
- `daemon_memory` table: daemon_name, memory_type, key, content JSONB, importance, access_count, expires_at
- UNIQUE constraint on (daemon_name, memory_type, key)
- Three indexes: daemon+type lookup, partial on expires_at, importance DESC
- `daemon_memory_stats` monitoring view with row counts, averages, expired counts

### WorkingMemory (Task 2)
- Pure Python `OrderedDict` with configurable `max_items` (default 100)
- True LRU eviction: oldest entry removed when at capacity, existing keys move to end on update
- Not in Postgres by design -- lost on restart

### DaemonMemoryStore (Task 3)
- Implements `MemoryStore` Protocol from `shared.contracts` -- verified with `isinstance()` check
- `save_episodic()`: 30-day default TTL, UPSERT via ON CONFLICT
- `cleanup_expired()`: deletes expired rows, supports per-daemon or global sweep
- `save_semantic()`: permanent memories, triggers compression at 10K row cap
- `_compress_memories()`: merges bottom 20% by importance using MAGMA or simple JSON fallback
- `load()`, `save()`, `cleanup()` -- protocol-compliant entry points

### MemoryCache (Task 4)
- Reads/writes `~/.objective-hertz/memory-cache/{daemon}.json`
- `save_with_cache()`: write-through to both Postgres and local JSON
- `rebuild_cache()`: rebuild from Postgres (authoritative source) for periodic reconciliation
- Sub-millisecond load times verified (well under 500ms target)

### IsolatedMemoryStore (Task 5)
- Reads `memory_domains` from `soul/dna/{daemon}.yaml` via `yaml.safe_load`
- `check_memory_access()` raises `PermissionError` for cross-daemon access
- Domain cache avoids repeated YAML file reads
- Verified: Titan (with domains titan_pipeline, titan_leads, titan_campaigns) cannot access ClawdBot memories

## Decisions Made

1. **OrderedDict for LRU** -- Python's `OrderedDict` provides `move_to_end()` and `popitem(last=False)` for true LRU behavior, unlike plain dict which only preserves insertion order.
2. **cleanup returns count via RETURNING** -- Using `RETURNING id` and counting rows instead of relying on cursor.rowcount (which `shared.db.execute` doesn't expose).
3. **MAGMA fallback to simple merge** -- If `shared.magma.semantic_merge` import fails, compression creates a consolidated JSON dict with all original entries. No data loss.
4. **In-process domain cache** -- DNA profiles rarely change, so caching `memory_domains` in a module-level dict avoids file I/O on every memory access.
5. **IsolatedMemoryStore as wrapper** -- Keeps DaemonMemoryStore clean and testable. Isolation is opt-in at the caller level.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functionality is fully wired. MAGMA compression has a real fallback (not a stub).

## Verification

- `isinstance(DaemonMemoryStore(), MemoryStore)` -- PASSES
- `ruff check shared/daemon_memory.py` -- CLEAN
- WorkingMemory LRU eviction -- verified (a evicted when max=3 and d inserted)
- Memory isolation -- verified (Titan blocked from ClawdBot access)
- Cache load time -- sub-millisecond (verified with perf_counter)

## Self-Check: PASSED

All 2 created files verified on disk. All 5 commit hashes found in git log.
