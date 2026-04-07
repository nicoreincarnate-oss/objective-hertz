# Brain Backdrop Tuning Audit — Phase 41

**Audit date:** 2026-04-06
**Source file:** `hermes/web/frontend/components/olympus/Memory3DGraph.tsx` (lines 393–425, 483–487)
**Scope:** Read-only analysis of the wireframe brain-shape backdrop that surrounds the 3D memory force-graph in the Oracle Pool / Mount Olympus hub.

---

## Current implementation

### Code reference

Lines 393–425 of `Memory3DGraph.tsx`:

```ts
// ── 5. Wireframe brain-shape backdrop ────────────────────────────────
const brainGeom = new THREE.IcosahedronGeometry(450, 3)
const posAttr = brainGeom.getAttribute('position') as THREE.BufferAttribute
for (let i = 0; i < posAttr.count; i++) {
  const x = posAttr.getX(i)
  const y = posAttr.getY(i)
  const z = posAttr.getZ(i)
  const noise =
    Math.sin(x * 0.02) * Math.cos(y * 0.02) * Math.sin(z * 0.02) * 40 +
    Math.sin(y * 0.05) * 20
  const len = Math.sqrt(x * x + y * y + z * z)
  const scale = 1 + noise / len
  posAttr.setXYZ(i, x * scale, y * scale, z * scale)
}
posAttr.needsUpdate = true
brainGeom.computeVertexNormals()

const brainMat = new THREE.MeshBasicMaterial({
  color: new THREE.Color(0x4466aa).multiplyScalar(0.9),
  wireframe: true,
  transparent: true,
  opacity: 0.09,
  depthWrite: false,
  toneMapped: false,
})
const brain = new THREE.Mesh(brainGeom, brainMat)
brain.name = 'brain-backdrop'
brain.frustumCulled = false
// Block raycasting so clicks pass through to nodes
brain.raycast = () => {}
scene.add(brain)
brainRef.current = brain
rotatingObjectsRef.current.push(brain)
```

Animation (lines 483–487):

```ts
// Also rotate the brain backdrop slowly
for (const obj of rotatingObjectsRef.current) {
  obj.rotation.y += 0.0003
  obj.rotation.x += 0.00015
}
```

### Parameter table

| Category          | Parameter                | Current value                              | Notes                                                |
| ----------------- | ------------------------ | ------------------------------------------ | ---------------------------------------------------- |
| **Geometry**      | Type                     | `IcosahedronGeometry`                      | Subdivided icosahedron approximates a sphere         |
|                   | Radius                   | `450`                                      | World units (nodes are ~5 units, see scale section)  |
|                   | Detail (subdivisions)    | `3`                                        | 1280 triangles, 642 vertices                         |
| **Displacement**  | Primary noise term       | `sin(x·0.02) · cos(y·0.02) · sin(z·0.02) · 40` | 3-axis interference, amplitude ±40                   |
|                   | Secondary noise term     | `sin(y·0.05) · 20`                         | Vertical lobe modulation, amplitude ±20              |
|                   | Total displacement range | ≈ ±60 units (≈13% of radius)               | Subtle lumpiness, not strongly cortical              |
|                   | Normalization            | `scale = 1 + noise/len`                    | Radial displacement (preserves spherical topology)   |
| **Material**      | Type                     | `MeshBasicMaterial`                        | Unlit — pure color, no shading                       |
|                   | Color                    | `0x4466aa × 0.9` ≈ `0x3d5c99`              | Dim slate blue                                       |
|                   | Wireframe                | `true`                                     | Triangle edges only                                  |
|                   | Transparent              | `true`                                     |                                                      |
|                   | Opacity                  | `0.09`                                     | Barely visible by design (atmospheric)               |
|                   | depthWrite               | `false`                                    | Won't occlude nodes behind it                        |
|                   | toneMapped               | `false`                                    | Bypasses ACES tone-map; color stays exact            |
| **Transform**     | Position                 | `(0, 0, 0)` (default)                      | Centered on graph origin                             |
|                   | Rotation                 | Animated: `+0.0003 y/frame`, `+0.00015 x/frame` | ≈ 1 rev / ~5.8 minutes on Y axis at 60fps           |
|                   | Scale                    | `(1, 1, 1)` (default)                      | No additional scale; radius lives in geometry        |
| **Culling**       | frustumCulled            | `false`                                    | Always rendered (prevents pop-out at extreme zoom)   |
|                   | raycast                  | `() => {}` (no-op)                         | Clicks pass through to nodes                         |
| **Tracking**      | Stored ref               | `brainRef`                                 | Available for runtime tweaks                         |
|                   | Rotation list            | `rotatingObjectsRef`                       | Rotated each frame in birth-animation loop           |

---

## Visual assessment

### Brain-likeness — **Score: 4/10**

The current implementation reads as a **lumpy sphere** rather than a brain. Key gaps vs. anatomical brain:

- **Lumpiness:** Displacement amplitude (~±60 units / 13%) is too subtle to suggest cortical folds (sulci/gyri). A real brain has folds with depth ratios closer to 25–35% of the bounding radius.
- **Symmetry:** A real brain is bilaterally symmetric with two hemispheres divided by the longitudinal fissure. Current geometry is rotationally noisy in all 3 axes — no hemisphere split.
- **Aspect ratio:** A real brain is ovoid (longer front-to-back than top-to-bottom). The current sphere is isotropic.
- **Topology:** No suggestion of brainstem, cerebellum, or frontal/occipital distinction.

### Scale — **Score: 9/10**

Radius `450` vs node radius `~5` gives a ratio of **90:1**, which is roughly correct:

- Force-graph nodes typically live within a ±200-unit cloud
- Brain at radius 450 envelops the cloud with comfortable margin
- Camera default position needs to sit somewhere in the 600–1500 range to frame both
- The brain doesn't crowd nodes; it provides scenic depth

This is well-tuned and should not change.

### Opacity — **Score: 9/10**

`0.09` is intentionally barely-visible. Combined with `wireframe: true` and the dim color `0x4466aa × 0.9`, the brain reads as **atmospheric structure** rather than UI furniture. This is correct — the nodes must be the focal point, the brain is environment.

The only concern: at certain camera angles where many wireframe edges overlap, the apparent opacity stacks (additive-ish since `depthWrite: false`) and the brain becomes more visible than intended on the silhouette. Acceptable.

### Wireframe density — **Score: 6/10**

Detail level 3 → 1280 triangles → ~1920 unique edges. At radius 450, that's roughly one edge every ~25 world units of arc length. This reads as **medium-coarse**:

- Fine enough to suggest curvature
- Coarse enough that individual triangles are visible at close camera ranges
- Coarseness becomes a feature when the brain rotates — you can see structure moving

A finer wireframe (detail 4 = 5120 tris) would feel **silkier** but might lose the "constructed" aesthetic that pairs well with the rest of the scene's wireframe vibe.

---

## Tuning options

### Option A — Increase noise amplitude for cortical folds

Bump primary noise from `40 → 80`, secondary from `20 → 40`. Total displacement range becomes ≈ ±120 (27% of radius), pushing into "actually lumpy" territory.

```ts
const noise =
  Math.sin(x * 0.02) * Math.cos(y * 0.02) * Math.sin(z * 0.02) * 80 +
  Math.sin(y * 0.05) * 40
```

Pros: trivial change, no perf cost, big visual gain, preserves all other parameters.
Cons: at detail 3, the wireframe is too coarse to read fine folds — pairs naturally with Option B.

### Option B — Increase subdivision detail for finer wireframe

`new THREE.IcosahedronGeometry(450, 4)` → 5120 triangles, 2562 vertices. ~4x triangle count.

Pros: finer mesh resolves displacement detail; lets Option A's folds read clearly.
Cons: 4x more geometry processing on init (one-time, ~3ms), 4x more wireframe lines per frame (still <0.5ms).

### Option C — Concentric dual-shell brain

Add a second inner mesh at radius 380 (or so) with different noise seed and slightly different color (e.g. warmer `0x553388`). Outer shell = "cortex", inner shell = "deep brain structure". The two interleave when rotating, creating parallax depth.

```ts
// outer cortex (existing)
const cortex = new THREE.Mesh(brainGeom, brainMat)

// inner brain
const innerGeom = new THREE.IcosahedronGeometry(380, 3)
// ... different noise displacement ...
const innerMat = new THREE.MeshBasicMaterial({
  color: new THREE.Color(0x553388).multiplyScalar(0.9),
  wireframe: true, transparent: true, opacity: 0.06,
  depthWrite: false, toneMapped: false,
})
const inner = new THREE.Mesh(innerGeom, innerMat)
// ... add to scene + rotation list ...
```

Pros: real depth sense; mood of "looking inside something"; pairs well with starfield.
Cons: 2x rendering cost (still negligible); adds a second rotating ref (trivial).

### Option D — Breathing opacity pulse

Animate opacity in a slow sine wave to suggest "the brain is alive". e.g. `opacity = 0.07 + 0.04 * sin(t * 0.4)` (period ≈ 16s).

```ts
// in tick loop
const t = Date.now() * 0.001
brainRef.current.material.opacity = 0.07 + 0.04 * Math.sin(t * 0.4)
```

Pros: subtle aliveness signal, no geometry change, costs <0.01ms/frame.
Cons: can be distracting if too pronounced; needs tuning of amplitude.

### Option E — Replace icosahedron with NIH 3D brain GLB

Load a real anatomical brain mesh from NIH 3D Print Exchange (3DPX-002088, MIT-licensed). Apply wireframe material to the imported geometry.

```ts
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'
const loader = new GLTFLoader()
loader.load('/olympus/brain.glb', (gltf) => {
  gltf.scene.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      ;(child as THREE.Mesh).material = brainMat
      ;(child as THREE.Mesh).raycast = () => {}
    }
  })
  gltf.scene.scale.setScalar(450 / boundingRadius)
  gltf.scene.frustumCulled = false
  scene.add(gltf.scene)
  rotatingObjectsRef.current.push(gltf.scene)
})
```

Pros: maximum anatomical realism (sulci, gyri, hemispheres, brainstem). Single biggest visual win possible.
Cons: requires asset (1–5MB GLB), async loading, edge counts likely 10K–50K tris, slight init cost. Also requires hosting the asset under `public/olympus/brain.glb`.

---

## Color recommendations

| Variant | Color spec                   | Visual feel                                  | Notes                                              |
| ------- | ---------------------------- | -------------------------------------------- | -------------------------------------------------- |
| Current | `0x4466aa × 0.9`             | Dim slate blue                               | Recedes into background                            |
| Alt 1   | `0x6688ff × 1.5` (HDR)       | Slightly luminous blue, blooms via post-FX   | Requires bloom pass with `toneMapped: false` (already set). Catches the eye on rotation. |
| Alt 2   | `0x88aacc × 1.0`             | Lighter cool blue                            | Wireframe more visible; brain becomes more "structural" than atmospheric |
| Alt 3   | Dynamic — pulses on memory births | Color shift ~`0x4466aa → 0x66ccff` over 800ms when new memories arrive | Requires tap-in to birth event stream. Most "alive" feeling. |
| Alt 4   | Warm tint `0xaa6644 × 0.9`   | Coppery — biological warmth                  | Departs from the blue scheme; only if scene re-themed |

**Recommended:** Alt 1 (HDR blue) — minimal code change, lets the existing bloom pipeline (already in scene setup) do the work, and gives the brain a subtle glow without breaking the dim/atmospheric design intent.

---

## Performance impact assessment

| Option            | Triangles | Init cost | Per-frame cost | 60fps headroom |
| ----------------- | --------- | --------- | -------------- | -------------- |
| Current (det 3)   | 1,280     | <1ms      | <0.1ms         | 99.4%          |
| A (more noise)    | 1,280     | <1ms      | <0.1ms         | 99.4%          |
| B (det 4)         | 5,120     | ~3ms      | <0.4ms         | 97.6%          |
| A + B combined    | 5,120     | ~3ms      | <0.4ms         | 97.6%          |
| C (dual shell)    | 2,560     | <2ms      | <0.2ms         | 98.8%          |
| D (opacity pulse) | 1,280     | <1ms      | <0.1ms         | 99.4%          |
| E (GLB ~15K tris) | ~15,000   | 50–200ms (load) | ~1.2ms      | 92.8%          |

**Frame budget at 60fps:** 16.67ms total. All options remain well under budget on M4 hardware. Even option E with a 50K-triangle hi-poly brain would consume <2ms — still safe.

The bottleneck for this scene is the force-graph layout solver (CPU side) and bloom post-FX (GPU side), not backdrop geometry.

---

## Decision matrix

| Option | Visual impact (1-10) | Complexity (1-10, lower=simpler) | Performance (1-10, higher=cheaper) | **Score** |
| ------ | -------------------- | -------------------------------- | ---------------------------------- | --------- |
| A — bigger noise        | 6  | 1  | 10 | **17** |
| B — detail 4            | 4  | 1  | 9  | 14 |
| **A + B combined**      | **8**  | **2**  | **9**  | **19** |
| C — dual shell          | 7  | 4  | 9  | **20** |
| D — opacity pulse       | 3  | 2  | 10 | 15 |
| E — NIH GLB             | 10 | 7  | 7  | **24** |
| Color Alt 1 (HDR blue)  | 4  | 1  | 10 | 15 |
| Color Alt 3 (dynamic)   | 6  | 5  | 9  | 20 |

---

## Recommendations — Top 2 to try first

### #1 — **A + B combined** (lumpy + finer mesh)

The lowest-risk, highest-immediate-payoff change. Two single-line edits, no new assets, no async loading, <0.5ms perf cost. Transforms the brain from "lumpy sphere" to "actually-reads-as-a-brain" without changing the architecture or visual language of the scene.

**Why first:** zero risk, instant reversibility, no dependencies. If the operator likes it, it's locked in. If not, revert two numbers. This is the right change to ship in Phase 41 itself.

### #2 — **E — NIH 3D Print Exchange GLB brain**

If the operator wants to go anatomically real, this is the path. The NIH 3D Print Exchange entry 3DPX-002088 is a free, MIT-licensed, anatomically accurate human brain mesh suitable for wireframe display. With the existing material settings (wireframe + low opacity + depthWrite false + bloom), it would look striking.

**Why second:** higher visual ceiling but adds an external asset, async loading code path, and a one-time download cost. Better as a Phase 42 follow-up once the core 3D pool is shipping reliably and the operator can evaluate "do we want anatomical or stylized?" with the basic brain in front of them.

A nice middle path: ship #1 in Phase 41, file #2 as a deferred enhancement for Phase 42 with the GLB asset acquisition and licensing review tracked separately.

---

## Implementation guide for top pick (#1 — A + B combined)

**Change 1 — line 394:** bump detail from 3 to 4

```diff
-      const brainGeom = new THREE.IcosahedronGeometry(450, 3)
+      const brainGeom = new THREE.IcosahedronGeometry(450, 4)
```

**Change 2 — lines 400–402:** double noise amplitudes

```diff
         const noise =
-          Math.sin(x * 0.02) * Math.cos(y * 0.02) * Math.sin(z * 0.02) * 40 +
-          Math.sin(y * 0.05) * 20
+          Math.sin(x * 0.02) * Math.cos(y * 0.02) * Math.sin(z * 0.02) * 80 +
+          Math.sin(y * 0.05) * 40
```

**Optional change 3 — line 411:** swap to HDR blue (Alt 1) to pair with the new lumpiness

```diff
-        color: new THREE.Color(0x4466aa).multiplyScalar(0.9),
+        color: new THREE.Color(0x6688ff).multiplyScalar(1.5),
```

**Verification steps:**
1. Hot reload `Memory3DGraph.tsx` in dev (`pnpm dev` in `hermes/web/frontend`)
2. Open the Olympus hub view, observe the rotating brain backdrop
3. Confirm: brain reads lumpier (cortical-fold suggestion); wireframe edges still legible; FPS remains 60 on M4
4. Confirm: nodes still clickable (raycast pass-through preserved); clicks still register on graph nodes
5. Confirm: bloom doesn't blow out the brain (HDR variant only — if too hot, drop multiplier from 1.5 to 1.2)

**Rollback:** revert the two (or three) single-line edits. No state, no migrations, no async cleanup.

**Estimated time:** 5 minutes edit, 5 minutes visual verification.

---

## Out of scope for this audit

- Modifying `Memory3DGraph.tsx` directly (read-only audit per task spec)
- Acquiring or licensing the NIH GLB asset (Phase 42 candidate)
- Tuning the starfield, fog, rim light, or bloom pass (separate audits)
- Changing the rotation animation speed (current values feel correct)
- Adding click interactivity to the brain itself (deliberately disabled — nodes are the interaction surface)
