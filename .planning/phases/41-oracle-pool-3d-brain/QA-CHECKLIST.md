# Phase 41 — Oracle's Pool 3D Brain — Manual QA Checklist

**Phase:** 41-oracle-pool-3d-brain
**Type:** Manual operator QA
**Estimated runtime:** ~22 minutes (full sweep)
**Run after:** All 5 sub-phases (41a–41e) merge to `intel-integration`

This checklist is to be executed by a human operator after the Oracle's Pool 3D brain
sub-phases land. Tick each box as you go. If any item fails, log it in
`.planning/phases/41-oracle-pool-3d-brain/QA-FAILURES.md` with reproduction steps and
surface to the operator before shipping.

---

## Section 1 — Smoke tests (~2 min)

- [ ] Dev server is running on `localhost:3000`
- [ ] Visit `/hub` — overworld map loads with 8 buildings
- [ ] Perseus cursor sprite visible and follows mouse
- [ ] Click "Oracle's Pool" building → loading screen appears
- [ ] Loading screen plays for ~2.5s with random background image
- [ ] Oracle's Pool room interior loads — pool art visible
- [ ] All 6 hotspots are visible with colored borders pulsing

---

## Section 2 — Brain graph activation (~1 min)

- [ ] Click the central "pool-center" hotspot
- [ ] Fullscreen 3D brain graph appears (NOT a pixel panel)
- [ ] Black overlay fades from full to transparent (camera-dive entrance)
- [ ] Title "THE POOL OF MEMORY" appears in pixel font at top center
- [ ] Concentric loader appears for ~600ms then fades out
- [ ] Either: nodes appear (if memories exist) OR empty state "THE POOL AWAITS THE FIRST MEMORY" shows

---

## Section 3 — Cinematic verification (~3 min, visual)

- [ ] Camera auto-rotates slowly around the brain
- [ ] Starfield visible in the distant background
- [ ] Wireframe brain icosahedron visible behind/around the nodes (low opacity)
- [ ] Nodes visibly GLOW (bloom effect) — not flat colored circles
- [ ] Each node has multiple layers: bright white core + colored sphere + soft halo
- [ ] Particle flow visible on edges — small cyan dots traveling source→target
- [ ] Hovering a node shows its label
- [ ] Nodes are color-coded by daemon:
  - [ ] green = hermes
  - [ ] blue = perseus
  - [ ] orange = titan
  - [ ] purple = clawdbot
  - [ ] gold = conway
  - [ ] red = ruflo

---

## Section 4 — HUD verification (~1 min)

- [ ] Top-left HUD shows "The Pool of Memory" header
- [ ] HUD shows NEURONS count (matches actual node count)
- [ ] HUD shows SYNAPSES count
- [ ] HUD shows LIVE SYNC ● green pulsing dot
- [ ] If new nodes appeared in last poll: +NEW counter visible in amber
- [ ] Bottom-right hint: "DRAG · ORBIT · CLICK A NEURON"
- [ ] Bottom-left: system stats badge

---

## Section 5 — Birth animation (~5 min)

Insert a fake memory via psql:

```sql
INSERT INTO memory_provenance (magma_node_id, source_daemon, source_table, source_record_id, visibility, ingested_at)
VALUES ('test_birth_' || extract(epoch from now()), 'hermes', 'test', 'manual_birth_test', 'tier1', now());
```

- [ ] Within 5 seconds, a new node appears in the graph
- [ ] The new node visibly PULSES GOLD for ~3 seconds
- [ ] Gold halo fades out smoothly via cosine ease
- [ ] After 3 seconds, node settles into normal glowing state
- [ ] +NEW counter increments in HUD

---

## Section 6 — Click & drawer (~2 min)

- [ ] Click any node → MemoryDetailDrawer slides in from the right
- [ ] Drawer shows memory ID, daemon, type, confidence
- [ ] Drawer body shows summary/body text
- [ ] Drawer close button (X) works
- [ ] ESC closes drawer (graph stays open)
- [ ] Click another node → drawer updates with new memory

---

## Section 7 — Empty state (~1 min)

- [ ] Disconnect Postgres OR test with empty `memory_provenance` table
- [ ] Brain graph shows: "THE POOL AWAITS THE FIRST MEMORY"
- [ ] No JS console errors
- [ ] HUD still renders with NEURONS=0, SYNAPSES=0

---

## Section 8 — Error state (~1 min)

- [ ] Stop the Hermes API server (kill uvicorn)
- [ ] Refresh the brain graph
- [ ] Bottom-left shows: "⚠ {error} — the pool is dry"
- [ ] No JS console errors (graceful failure)

---

## Section 9 — Exit cascade (~1 min)

- [ ] Press ESC → MemoryDetailDrawer closes (if open)
- [ ] Press ESC → 3D brain graph closes
- [ ] Press ESC → Oracle's Pool room closes
- [ ] Press ESC → returns to overworld
- [ ] Perseus cursor visible throughout entire cascade

---

## Section 10 — Performance (~5 min)

- [ ] Open Chrome DevTools → Performance tab → record 30 seconds
- [ ] Frame rate is ≥ 30fps (target 60fps)
- [ ] No memory leak: open Memory tab, take 3 snapshots over 5 minutes, snapshot size should NOT grow unbounded
- [ ] CPU usage during idle (no interaction) is < 30%
- [ ] Polling: Network tab shows `/api/memory/graph?depth=3&limit=500` every ~5s

---

## Section 11 — Cross-platform (if available)

- [ ] Mac Safari: bloom renders correctly
- [ ] Mac Chrome: bloom renders correctly
- [ ] Mac Firefox: bloom renders correctly (may differ slightly)

---

## Pass criteria

- **Sections 1–9 must ALL pass** for v1 ship
- **Sections 10–11** are nice-to-have but not blocking
- If any section fails: log the failure in
  `.planning/phases/41-oracle-pool-3d-brain/QA-FAILURES.md` and surface to operator
  before merging to main

---

## Sign-off

| Field | Value |
|-------|-------|
| Operator | _________________ |
| Date run | _________________ |
| Build / commit | _________________ |
| Sections 1–9 result | ☐ PASS  ☐ FAIL |
| Sections 10–11 result | ☐ PASS  ☐ FAIL  ☐ SKIPPED |
| Ship decision | ☐ SHIP  ☐ HOLD |
| Notes | _________________ |
