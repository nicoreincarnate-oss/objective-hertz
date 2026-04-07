# Phase 42 — MAGMA Runtime QA Checklist

Manual operator verification after Phase 42 ships. Walk through every section in order. Stop and investigate the first time you hit a ❌.

## 1. Module presence (8 modules)

Run:
```bash
python scripts/verify_magma_runtime.py --verbose
```

Confirm all modules show ✅ in the "Phase 42 modules" check:

- [ ] `scripts.bootstrap_magma`
- [ ] `shared.magma_writer`
- [ ] `shared.consolidator`
- [ ] `shared.procedural_extractor`
- [ ] `shared.edge_inference`
- [ ] `shared.magma_edge_types`
- [ ] `shared.operator_memory_hook`
- [ ] `shared.magma`

## 2. Bootstrap dry run

```bash
python scripts/bootstrap_magma.py --dry-run
```

Expected:
- [ ] Exits 0
- [ ] Reports projected node count > 50
- [ ] No DB writes occurred (verify with `SELECT COUNT(*) FROM magma_nodes;` — should be unchanged)

## 3. Bootstrap actual run

```bash
python scripts/bootstrap_magma.py
```

Expected:
- [ ] Exits 0
- [ ] Reports number of nodes inserted
- [ ] No traceback on duplicates (idempotent)
- [ ] Re-running produces 0 new inserts (idempotency confirmed)

## 4. SQL verification

Run in psql:
```sql
SELECT COUNT(*) FROM magma_nodes;
SELECT COUNT(*) FROM magma_edges;
SELECT daemon, COUNT(*) FROM magma_nodes GROUP BY daemon ORDER BY 2 DESC;
```

Expected:
- [ ] `magma_nodes` count > 50
- [ ] `magma_edges` count > 0
- [ ] At least 3 distinct daemons represented

## 5. Brain graph rendering (Hub UI)

Steps:
1. Visit `http://localhost:8500/hub` (Hermes War Room)
2. Click the **Oracle Pool** card
3. Click the pool center to expand the brain graph

Expected:
- [ ] Graph renders without console errors
- [ ] ≥50 nodes visible
- [ ] Edges connect related nodes
- [ ] Node colors/sizes reflect daemon and importance

## 6. Live event → node propagation

Insert a synthetic event via the operator chat or API:
```bash
curl -X POST http://localhost:8500/api/memory/event \
  -H "Content-Type: application/json" \
  -d '{"daemon":"test","event_type":"qa_test","payload":{"msg":"phase42 qa"}}'
```

Expected:
- [ ] Within 5 seconds, a new node appears in the brain graph
- [ ] `SELECT * FROM magma_nodes ORDER BY created_at DESC LIMIT 1;` shows the test event

## 7. Consolidator manual run

```bash
python -c "from shared.consolidator import consolidate_recent_events; consolidate_recent_events()"
```

Expected:
- [ ] Exits 0
- [ ] At least one `lesson_learned` (or equivalent semantic) node appears in `magma_nodes`
- [ ] Re-running is idempotent (no duplicate lessons)

## 8. Verification script — final pass

```bash
python scripts/verify_magma_runtime.py
```

Expected:
- [ ] OVERALL HEALTH: HEALTHY (or DEGRADED with only Ollama/API soft warnings)
- [ ] No CRITICAL status
- [ ] Exit code 0 (or 2 if Ollama/API down — acceptable in dev)

## 9. Regression suite

```bash
PYTHONPATH=. python3 -m pytest tests/test_phase42_magma_runtime.py -v
PYTHONPATH=. python3 -m pytest tests/ -q --ignore=tests/test_phase42_magma_runtime.py
```

Expected:
- [ ] Phase 42 smoke tests all pass (no skips except known-pending modules)
- [ ] No new failures in the broader test suite

## 10. Operator sign-off

- [ ] Operator (Mauricio) has visually inspected the brain graph
- [ ] No console errors in browser dev tools
- [ ] No daemon crash loops in `make logs`
- [ ] HANDOFF.md updated with Phase 42 completion notes

---

**On any ❌:** capture the failing command output, post to `#phase42-qa` with the verification script output (`python scripts/verify_magma_runtime.py --verbose`), and tag the owning agent (A1-A8).
