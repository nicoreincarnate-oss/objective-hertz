# Phase 30: Governance + Safety Foundation — VERIFICATION

**Status:** COMPLETE
**Date:** 2026-04-05

---

## Task 1: Multi-Agent Governance Layer (MAS)

### 1a. Agent Policy Engine
- [x] `AGENT_POLICIES` dict in `shared/middleware.py` with 5 agents (titan, clawdbot, hermes, perseus, ruflo)
- [x] Each agent has `allowed_targets`, `max_cost_per_dispatch`, `allowed_capabilities`
- [x] Perseus has wildcard `["*"]` capability access
- [x] `governance_check()` function validates source, target, capability, and cost
- [x] Feature-flag gated by `MAS_GOVERNANCE` (default true)
- [x] `GovernanceViolation` exception class

### 1b. Audit Trail
- [x] `_dispatch_a2a_task()` in `shared/comms.py` wrapped with governance check + audit logging
- [x] `_fire_audit()` helper for fire-and-forget audit log inserts
- [x] `_log_governance_audit()` in `shared/middleware.py` writes to `agent_audit_log` table
- [x] Correlation ID extracted from payload `_meta` context
- [x] Policy violations logged as "denied" and block dispatch

### 1c. Protocol Validation
- [x] `validate_request()` in `openjarvis/mcp/protocol.py` — JSON-RPC 2.0 compliance, size limits, field validation
- [x] `validate_response()` — result/error exclusivity, error structure validation
- [x] `MCPValidationError` exception with code and message
- [x] `MAX_PAYLOAD_BYTES = 51200` (50KB per AEGIS audit requirement)

## Task 2: POMDP Safety Bounds

### 2a. Retrieval Belief State
- [x] `RetrievalBeliefState` dataclass in `shared/magma.py`
- [x] Fields: query, retrieved_ids, confidence, uncertainty_sources, retrieval_history, poisoning_score, step_count
- [x] `pomdp_retrieve()` wrapper around `magma_retrieve()` with belief tracking
- [x] Halt conditions: poisoning > 0.7, confidence > threshold, max steps

### 2b. Memory Poisoning Defense
- [x] `score_anomaly()` function scoring 4 signals:
  - Freshness (nodes <1hr with no links): +0.3
  - Source concentration (>80% from same source): +0.2
  - Semantic contradiction (high+low confidence on same entity): +0.4
  - Bulk injection (>5 nodes in same minute): +0.3
- [x] Score capped at 1.0

### 2c. Safety Risk Scorer
- [x] `score_action_risk()` function in `shared/magma.py`
- [x] `mutate_code` -> always 1.0 (blocked)
- [x] `mutate_config` on budget/security keys -> 0.9
- [x] `mutate_prompt` scaling with delta_size
- [x] Protected paths (wallet.py, llm_client.py, security/) -> +0.5
- [x] Result capped to [0.0, 1.0]

## Task 3: T2 Model Selection

### 3a. Pass@k Logic
- [x] `T2Selection` dataclass with model, passes, selection_strategy, verifier
- [x] `select_model_t2()` returns `(ModelSelection, T2Selection)`
- [x] T2 decision matrix: classification (Haiku x3), email_subject (Haiku x5), code (Sonnet x2)
- [x] Feature-flag gated by `T2_MODEL_SELECT` (default true)

### 3b. Task Verifiability Map
- [x] `_TASK_TYPE_HINTS` extended with `verifiable`, `pass_k_eligible`, `verifier` fields
- [x] 5 verifiable tasks: classification, extraction, email_subject, lead_scoring, code
- [x] 4 non-verifiable tasks: email, proposal, architecture, summarization

### 3c. Concurrent Execution
- [x] `execute_with_t2()` runs k candidates via `asyncio.gather()`
- [x] Strategies: "best" (score sorted), "majority" (most common), "first_passing" (first >0.5)
- [x] Exception handling for generation failures
- [x] `T2_MAX_PASSES` cap (default 5)

## Database Migration

- [x] `scripts/migrations/042-governance-safety.sql`
- [x] `agent_audit_log` table with indices on correlation_id, source_agent+time, policy_result
- [x] `retrieval_sessions` table with indices on session_id, flagged
- [x] Feature flag inserts for MAS_GOVERNANCE, POMDP_SAFETY_BOUNDS, T2_MODEL_SELECT

## Test Results

```
78 passed in 0.17s
```

| Test File | Tests | Status |
|-----------|-------|--------|
| test_phase30_governance.py | 16 | PASS |
| test_phase30_mcp_validation.py | 21 | PASS |
| test_phase30_pomdp_safety.py | 20 | PASS |
| test_phase30_t2_model_select.py | 21 | PASS |

## Feature Flags

| Flag | Default | Location |
|------|---------|----------|
| `MAS_GOVERNANCE` | true | env + system_config |
| `POMDP_SAFETY_BOUNDS` | true | env |
| `T2_MODEL_SELECT` | true | env |

## Files Modified

- `shared/middleware.py` — AGENT_POLICIES, governance_check(), _log_governance_audit(), GovernanceViolation
- `shared/comms.py` — _dispatch_a2a_task() governance+audit wrapper, _fire_audit()
- `openjarvis/mcp/protocol.py` — validate_request(), validate_response(), MCPValidationError
- `shared/magma.py` — RetrievalBeliefState, score_anomaly(), pomdp_retrieve(), score_action_risk()
- `shared/model_selector.py` — T2Selection, _TASK_TYPE_HINTS verifiability, select_model_t2(), execute_with_t2()

## Files Created

- `scripts/migrations/042-governance-safety.sql`
- `tests/test_phase30_governance.py`
- `tests/test_phase30_mcp_validation.py`
- `tests/test_phase30_pomdp_safety.py`
- `tests/test_phase30_t2_model_select.py`
- `.planning/phases/30-governance-safety-foundation/VERIFICATION.md`

## Success Criteria Check

1. [x] A2A dispatches pass through governance middleware
2. [x] Policy violations logged and blocked (test: ClawdBot -> Perseus denied)
3. [x] POMDP retrieval wraps magma_retrieve with belief state
4. [x] Safety risk scorer: score_action_risk("mutate_code", "wallet.py", 100, {}) returns 1.0
5. [x] T2 model selection: classification -> Haiku x3 (vs Sonnet x1)
6. [x] All 3 feature flags default to true
7. [x] All 78 Phase 30 tests pass
8. [x] ruff clean on Phase 30 additions (pre-existing E501/I001 in parent files excluded)
