# Phase 41 — Deferred Items

## Sub-phase 1

### Pre-existing tsc errors (out of scope)

`pnpm tsc --noEmit` in `hermes/web/frontend` reports 37 pre-existing TypeScript
errors that are NOT caused by Sub-phase 1 work and were present before this
sub-phase started. They are entirely contained in two files:

- `components/olympus/OlympusGame.tsx`
- `components/olympus/scenes/OlympusScene.ts`

Root cause: the `phaser` package is imported but not installed in the
frontend's `package.json`. Both files are part of the broader Olympus game
work (Phase 40) and are unrelated to the 3D memory graph (Phase 41). They
should be addressed by the Phase 40 owner — either by installing `phaser`
and `@types/phaser` or by removing the unused game scaffolding.

Scope rule: Sub-phase 1 only fixes errors directly caused by its own
changes. After installing `@types/three` and tightening the `fgRef` typing,
both `Memory3DGraph.tsx` and `Graph3DPanel.tsx` are clean (0 errors).

## Sub-phase 6 — Future Enhancements Deferred from Phase 41 v1

These features were considered for v1 but explicitly deferred to keep scope
tight and ship the core 3D brain experience first. None of them block v1.

- **WebSocket upgrade** — replace polling with a live push channel. Full
  spec lives in `WEBSOCKET-UPGRADE.md` (~7 hours engineering). Defer until
  we see polling cost or latency become a real problem.
- **Search bar inside the brain graph** — text input that filters visible
  nodes by label/content match. Requires a small overlay UI and a node
  filter pipeline through the force graph.
- **Daemon filter chips** — toggle visibility of nodes by source daemon
  (Hermes / Perseus / Titan / ClawdBot / Conway / etc). UI affordance plus
  the same filter pipeline as the search bar.
- **Cluster highlighting** — visual grouping by memory type (episodic /
  semantic / procedural / feedback). Could use convex hulls, halo shaders,
  or color banding.
- **Fly-to-node camera animation** — on node click, smoothly animate the
  camera to frame that node instead of hard-cutting. `react-force-graph-3d`
  exposes a `cameraPosition()` method that can do this.
- **Audio synthesis** — ambient drone that pulses with graph activity
  (memory writes, retrievals, edge formation). Web Audio API + simple
  oscillator chain.
- **VR mode** — vasturiano ships a VR variant of `react-force-graph-3d`.
  Drop-in replacement, but needs a WebXR-capable browser and headset to
  validate. Fun but not revenue-relevant.
- **Brain backdrop tuning options** — exposed knobs for bloom intensity,
  particle density, fog falloff, color grading. Full spec in
  `BRAIN-BACKDROP-TUNING.md`.
- **Approach B upgrade** — full React Three Fiber scene with
  `three-forcegraph` instead of the current `react-force-graph-3d` wrapper.
  Only worth doing if we need GPU compute, custom shaders, or volumetric
  effects that the wrapper can't expose. Hold until a concrete need
  appears.

## Pre-existing baseline tsc errors (Phase 40 — NOT Phase 41)

The 37 tsc errors documented in the Sub-phase 1 section above are baseline
state from Phase 40 and predate Phase 41 entirely. Recap:

- 37 errors total across:
  - `hermes/web/frontend/components/olympus/OlympusGame.tsx`
  - `hermes/web/frontend/components/olympus/scenes/OlympusScene.ts`
- Root cause: the `phaser` package is referenced but not installed.
- These existed BEFORE Phase 41 began.
- Phase 41 explicitly excludes them from its validation gates — Phase 41
  validation only counts errors in files Phase 41 touches.
- Recommended fix in a future Phase 40.x: either install `phaser`
  (`pnpm add phaser @types/phaser`) or delete the OlympusGame component
  entirely (the new pixel-art `/hub` route replaces the concept).

## Decision: Phase 40 OlympusGame cleanup

Recommend a separate **Phase 40.1** dedicated to clearing this baseline.
Two options:

- **Option A** — install `phaser` + `@types/phaser` and complete the
  Phaser-based OlympusGame scene. Keeps the existing scaffolding alive.
- **Option B** — delete `OlympusGame.tsx`, `scenes/OlympusScene.ts`, and
  the entire `components/olympus/scenes/` directory. The new pixel-art
  `/hub` route replaces this concept entirely, so the scaffolding has no
  consumer.

**Recommendation: Option B.** The pivot to pixel art Mount Olympus on the
`/hub` route makes the Phaser scaffolding dead code. Deleting it is
cleaner, removes the 37-error baseline in one stroke, and shrinks the
frontend bundle. Option A only makes sense if we change our minds about
the pixel-art direction.

## Performance benchmarks not yet captured

We need real numbers before we promise a node-count ceiling. Defer
benchmarking until after Wave 1 merge; capture in Phase 41.1 if numbers
look concerning.

- Brain graph at **50 nodes** (likely current state in dev)
- Brain graph at **500 nodes** (target steady-state capacity)
- Brain graph at **5000 nodes** (stress test, find the cliff)
- GPU profiling on **Mac M4** (target hardware) — frame time, GPU memory,
  bloom pass cost
- Capture FPS, frame variance, and memory footprint at each tier

## Cross-platform testing not yet done

Bloom and post-processing may render slightly differently per GPU and
browser. We have not yet validated on:

- Mac Safari
- Mac Chrome
- Mac Firefox
- Windows Chrome

Defer until v1 ships. File any rendering inconsistencies in Phase 41.1.
