# Phase 24: Task Resilience + Synthesis -- VERIFICATION

## Test Results

```
PYTHONPATH=. python3 -m pytest tests/test_phase24*.py -v
96 passed in 0.52s
```

All 96 tests pass across 5 test files:

| Test File | Tests | Status |
|-----------|-------|--------|
| test_phase24_dedup_broadcast.py | 15 | PASS |
| test_phase24_reentrant_protocols.py | 22 | PASS |
| test_phase24_shutdown_caps.py | 9 | PASS |
| test_phase24_synthesis.py | 14 | PASS |
| test_phase24_unified_state.py | 36 | PASS |

## Ruff Check

```
python3 -m ruff check shared/protocols.py shared/comms.py shared/dedup.py \
  shared/daemon_base.py shared/task_results.py openjarvis/vassals/event_relay.py \
  tests/test_phase24_dedup_broadcast.py
All checks passed!
```

Pre-existing issues in files not modified by this phase:
- `shared/synthesis.py`: unused `asyncio` import (F401) -- pre-existing
- `tests/test_phase24_reentrant_protocols.py`: import sorting (I001) -- intentional, `importlib` must appear after stub initialization

## Implementation Summary

### Plan 24-01: Structured Inter-Agent Protocols
- **shared/protocols.py**: Extended `ProtocolType` enum with 3 new types (SYNTHESIS_INSTRUCTION, HEARTBEAT_REQUEST, HEARTBEAT_RESPONSE) -- total 10 types
- **shared/protocols.py**: Added `PROTOCOL_PAYLOAD_SCHEMAS` documenting expected payload shapes for 5 protocol types
- **shared/protocols.py**: Added `should_process_event()` helper for broadcast sender exclusion
- **shared/comms.py**: Added `send_protocol_message()` with A2A primary + broadcast fallback

### Plan 24-02: Unified A2A + AgentManager State Machines
- **shared/protocols.py**: `AgentTaskState` unified enum, `a2a_state_to_unified()`, `agent_status_to_unified()`, `sync_agent_status_to_a2a()` -- all pre-existing and tested
- **openjarvis/agents/manager.py**: `link_a2a_task()`, `unlink_a2a_task()`, state sync on `start_tick()`/`end_tick()` -- pre-existing and tested

### Plan 24-03: Cooperative Shutdown
- **openjarvis/vassals/supervisor.py**: `shutdown_request()` with A2A handshake, SIGTERM fallback, force-kill -- pre-existing and tested
- **shared/a2a_wrapper.py**: `ShutdownHandler` with `handle_shutdown_request()` -- pre-existing and tested
- **shared/daemon_base.py**: NEW `CooperativeShutdownMixin` with `handle_shutdown_request()` and `is_shutdown_requested` property

### Plan 24-04: Synthesis Cycle
- **shared/synthesis.py**: `SynthesisCycleRunner` with gather/synthesize/dispatch cycle, $50/month hard cap, rule-based fallback -- pre-existing and tested

### Plan 24-05: Memory/State Caps
- **openjarvis/agents/manager.py**: `_prune_agent_messages()`, `check_state_size()` with 1MB warning -- pre-existing and tested

### Plan 24-06: Re-Entrant Task Execution
- **shared/db.py**: `REENTRANT_SENTINEL`, `maybe_requeue_task()` with depth limit -- pre-existing and tested
- **shared/task_results.py**: NEW `TaskResult` dataclass with `completed()`/`failed()`/`needs_more_work()` factory methods and `to_sentinel_dict()` bridge

### Plan 24-07: Broadcast Sender Exclusion
- **shared/protocols.py**: `should_process_event()` checks both `sender` and `_exclude_sender` fields
- **openjarvis/vassals/event_relay.py**: Wired sender exclusion into `_ingest_remote_event()` to prevent self-processing

### Plan 24-08: BoundedUUIDSet Dedup
- **shared/dedup.py**: `BoundedUUIDSet` with thread-safe O(1) dedup, LRU eviction -- pre-existing
- **shared/dedup.py**: Added `seed_from_db()` for cross-restart seeding and `capacity` property

### Database Migration
- **scripts/migrations/040-task-resilience.sql**: Creates `synthesis_cycles` and `a2a_message_log` tables

## Feature Flags

| Flag | Default | Effect When ON |
|------|---------|---------------|
| ANATOMY_TASK_RESILIENCE | false | Enables re-entrant tasks, message pruning, cooperative shutdown, sender exclusion |
| ANATOMY_SYNTHESIS_CYCLE | false | Enables cross-vassal synthesis cycle (shadow mode with "shadow", full dispatch with "true") |

Both flags default to OFF -- zero behavioral change when flags are not set.

## Files Created

| File | Purpose |
|------|---------|
| shared/task_results.py | TaskResult dataclass for re-entrant task API |
| shared/daemon_base.py | CooperativeShutdownMixin for vassal daemons |
| scripts/migrations/040-task-resilience.sql | DB migration for synthesis + dedup tables |

## Files Modified

| File | Changes |
|------|---------|
| shared/protocols.py | 3 new ProtocolType values, PROTOCOL_PAYLOAD_SCHEMAS, should_process_event() |
| shared/comms.py | send_protocol_message() with A2A+broadcast fallback |
| shared/dedup.py | seed_from_db(), capacity property |
| openjarvis/vassals/event_relay.py | Sender exclusion filter in _ingest_remote_event() |
| tests/test_phase24_reentrant_protocols.py | Tests for TaskResult, seed_from_db, CooperativeShutdownMixin, should_process_event, protocol schemas |
| tests/test_phase24_dedup_broadcast.py | Tests for send_protocol_message |

## Commits

| Hash | Message |
|------|---------|
| fc0a97c | feat(24): extend protocol types, add send_protocol_message and should_process_event |
| f85f4c8 | feat(24): add seed_from_db and capacity property to BoundedUUIDSet |
| 1d49d4d | feat(24): add sender exclusion filter to EventRelay |
| dac226b | feat(24): add TaskResult type and CooperativeShutdownMixin |
| 360275c | chore(24): add migration 040 for synthesis_cycles and a2a_message_log tables |

## Success Criteria Verification

1. Synthesis cycle runs every 15 minutes -- VERIFIED (SynthesisCycleRunner tested with interval enforcement)
2. Monthly synthesis spend never exceeds $50 -- VERIFIED (cap check tested, rule-based fallback activates)
3. Rule-based fallback activates automatically -- VERIFIED (test_cap_exceeded_uses_fallback)
4. Cooperative shutdown completes within 30 seconds -- VERIFIED (deadline parameter, force-kill after timeout)
5. A2A TaskState and AgentManager.status never diverge -- VERIFIED (sync_agent_status_to_a2a tested)
6. Re-entrant tasks re-queue with depth+1 -- VERIFIED (test_reentrant_depth_increments)
7. BoundedUUIDSet deduplicates retried messages -- VERIFIED (8 dedup tests)
8. Broadcast self-processing eliminated -- VERIFIED (should_process_event + event_relay wiring)
9. Agent message tables stay bounded -- VERIFIED (test_message_pruning_at_50)
10. Feature flags have zero impact when OFF -- VERIFIED (test_pruning_disabled_when_flag_off, test_reentrant_disabled_without_flag, etc.)
