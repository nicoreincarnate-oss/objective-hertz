/**
 * AmbientParticles — Phase 43 sub-phase 4
 *
 * Flowing ambient particle field for the anatomical brain scene. 30k default
 * particles drift on per-particle velocities, reflecting off a configurable
 * bounding box. CPU-side physics with a single BufferAttribute position upload
 * per frame — this scales comfortably to 30k–100k on M-series Macs while
 * keeping the implementation simple and robust (no FBO ping-pong plumbing).
 *
 * Pure module (no React) despite .tsx extension — consistent with sibling
 * brain modules in this directory.
 */

import * as THREE from 'three'

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface AmbientParticlesHandle {
  /** THREE.Points object — add directly to your scene */
  points: THREE.Points
  /** Update the reflection bounds. Particles already outside will be clamped. */
  setBounds: (box: THREE.Box3) => void
  /** Rebuild the geometry with a new particle count (clamped to [1, maxCount]) */
  setParticleCount: (count: number) => void
  /** Step simulation forward by deltaSec. Renderer param reserved for future GPGPU. */
  tick: (deltaSec: number, renderer: THREE.WebGLRenderer) => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_COUNT = 30000
const MAX_COUNT = 100000
const VELOCITY_RANGE = 8 // world-units per second — gentle drift
const RANDOMIZE_INTERVAL_FRAMES = 60
const RANDOMIZE_FRACTION = 0.05

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createAmbientParticles(initialCount: number = DEFAULT_COUNT): AmbientParticlesHandle {
  // Clamp requested count into [1, MAX_COUNT]
  const clampCount = (n: number): number => Math.max(1, Math.min(MAX_COUNT, Math.floor(n)))

  // Mutable state — capacity is allocated to MAX_COUNT so we can resize cheaply
  // without reallocating the underlying typed arrays.
  const capacity = MAX_COUNT
  const positions = new Float32Array(capacity * 3)
  const velocities = new Float32Array(capacity * 3)
  let activeCount = clampCount(initialCount)

  // Bounds (default ±200 cube)
  const bounds = new THREE.Box3(
    new THREE.Vector3(-200, -200, -200),
    new THREE.Vector3(200, 200, 200),
  )

  // Helpers operating on the current bounds
  const randomInBounds = (out: [number, number, number]): void => {
    out[0] = bounds.min.x + Math.random() * (bounds.max.x - bounds.min.x)
    out[1] = bounds.min.y + Math.random() * (bounds.max.y - bounds.min.y)
    out[2] = bounds.min.z + Math.random() * (bounds.max.z - bounds.min.z)
  }

  const randomVelocity = (): number => (Math.random() * 2 - 1) * VELOCITY_RANGE

  // Seed positions + velocities for the initial active slice
  const seedRange = (start: number, end: number): void => {
    const scratch: [number, number, number] = [0, 0, 0]
    for (let i = start; i < end; i++) {
      randomInBounds(scratch)
      positions[i * 3 + 0] = scratch[0]
      positions[i * 3 + 1] = scratch[1]
      positions[i * 3 + 2] = scratch[2]
      velocities[i * 3 + 0] = randomVelocity()
      velocities[i * 3 + 1] = randomVelocity()
      velocities[i * 3 + 2] = randomVelocity()
    }
  }
  seedRange(0, activeCount)

  // Geometry — the position attribute is a *view* into the backing array sized
  // to the active count, so draw cost tracks activeCount (not capacity).
  const geometry = new THREE.BufferGeometry()
  let positionAttribute = new THREE.BufferAttribute(
    positions.subarray(0, activeCount * 3),
    3,
  )
  positionAttribute.setUsage(THREE.DynamicDrawUsage)
  geometry.setAttribute('position', positionAttribute)
  geometry.setDrawRange(0, activeCount)

  const material = new THREE.PointsMaterial({
    color: new THREE.Color(0x88c8ff),
    size: 0.5,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.4,
    blending: THREE.AdditiveBlending,
    toneMapped: false,
    depthWrite: false,
  })

  const points = new THREE.Points(geometry, material)
  points.name = 'AmbientParticles'
  points.frustumCulled = false

  // Frame counter for periodic velocity randomization
  let frameCounter = 0

  // -------------------------------------------------------------------------
  // setBounds
  // -------------------------------------------------------------------------
  const setBounds = (box: THREE.Box3): void => {
    bounds.copy(box)
    // Clamp any currently-out-of-bounds particles back inside
    for (let i = 0; i < activeCount; i++) {
      const ix = i * 3
      if (positions[ix + 0] < bounds.min.x) positions[ix + 0] = bounds.min.x
      else if (positions[ix + 0] > bounds.max.x) positions[ix + 0] = bounds.max.x
      if (positions[ix + 1] < bounds.min.y) positions[ix + 1] = bounds.min.y
      else if (positions[ix + 1] > bounds.max.y) positions[ix + 1] = bounds.max.y
      if (positions[ix + 2] < bounds.min.z) positions[ix + 2] = bounds.min.z
      else if (positions[ix + 2] > bounds.max.z) positions[ix + 2] = bounds.max.z
    }
    positionAttribute.needsUpdate = true
  }

  // -------------------------------------------------------------------------
  // setParticleCount
  // -------------------------------------------------------------------------
  const setParticleCount = (count: number): void => {
    const next = clampCount(count)
    if (next === activeCount) return

    // Re-seed the entire active range for a clean random distribution
    seedRange(0, next)
    activeCount = next

    // Swap the position attribute to a new view sized to the new active count.
    // Reusing the backing array (`positions`) means no allocation for grow/shrink
    // within capacity.
    positionAttribute = new THREE.BufferAttribute(
      positions.subarray(0, activeCount * 3),
      3,
    )
    positionAttribute.setUsage(THREE.DynamicDrawUsage)
    geometry.setAttribute('position', positionAttribute)
    geometry.setDrawRange(0, activeCount)
    positionAttribute.needsUpdate = true
  }

  // -------------------------------------------------------------------------
  // tick
  // -------------------------------------------------------------------------
  const tick = (deltaSec: number, _renderer: THREE.WebGLRenderer): void => {
    // Guard against pathological deltas (tab resume, first frame, etc.)
    if (!Number.isFinite(deltaSec) || deltaSec <= 0) return
    const dt = Math.min(deltaSec, 1 / 15) // cap at ~67ms to avoid tunneling

    const minX = bounds.min.x
    const maxX = bounds.max.x
    const minY = bounds.min.y
    const maxY = bounds.max.y
    const minZ = bounds.min.z
    const maxZ = bounds.max.z

    for (let i = 0; i < activeCount; i++) {
      const ix = i * 3

      let px = positions[ix + 0] + velocities[ix + 0] * dt
      let py = positions[ix + 1] + velocities[ix + 1] * dt
      let pz = positions[ix + 2] + velocities[ix + 2] * dt

      // Reflect off bounds — flip velocity and clamp position
      if (px < minX) {
        px = minX
        velocities[ix + 0] = -velocities[ix + 0]
      } else if (px > maxX) {
        px = maxX
        velocities[ix + 0] = -velocities[ix + 0]
      }
      if (py < minY) {
        py = minY
        velocities[ix + 1] = -velocities[ix + 1]
      } else if (py > maxY) {
        py = maxY
        velocities[ix + 1] = -velocities[ix + 1]
      }
      if (pz < minZ) {
        pz = minZ
        velocities[ix + 2] = -velocities[ix + 2]
      } else if (pz > maxZ) {
        pz = maxZ
        velocities[ix + 2] = -velocities[ix + 2]
      }

      positions[ix + 0] = px
      positions[ix + 1] = py
      positions[ix + 2] = pz
    }

    // Every RANDOMIZE_INTERVAL_FRAMES frames, nudge 5% of particles with fresh
    // velocities for organic non-repeating motion.
    frameCounter++
    if (frameCounter >= RANDOMIZE_INTERVAL_FRAMES) {
      frameCounter = 0
      const toRandomize = Math.max(1, Math.floor(activeCount * RANDOMIZE_FRACTION))
      for (let k = 0; k < toRandomize; k++) {
        const i = Math.floor(Math.random() * activeCount)
        const ix = i * 3
        velocities[ix + 0] = randomVelocity()
        velocities[ix + 1] = randomVelocity()
        velocities[ix + 2] = randomVelocity()
      }
    }

    positionAttribute.needsUpdate = true
  }

  return {
    points,
    setBounds,
    setParticleCount,
    tick,
  }
}
