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
