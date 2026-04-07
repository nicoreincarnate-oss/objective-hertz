/**
 * AmbientNeurons — Phase 43 sub-phase 7
 *
 * High-density "filler" neurons (default 30k) that give the brain an always-alive,
 * organic background pulse. Distinct from InstancedNeurons (which renders the
 * addressable, picker-enabled daemon nodes) — ambient neurons are non-interactive,
 * phase-modulated, and optimized for the hot per-frame color update path.
 *
 * Pure module (no React) despite .tsx extension — kept for consistency with the
 * sibling brain modules in this directory.
 */

import * as THREE from 'three'

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface AmbientRegionInput {
  daemon: string
  center: THREE.Vector3
  radius: number
  color: string
}

export interface AmbientNeuronsHandle {
  /** Add this group to your THREE.Scene */
  group: THREE.Group
  /** Redistribute neurons across the given regions (spheres in world space * 200). */
  setRegions: (regions: AmbientRegionInput[]) => void
  /** Rebuild the InstancedMesh with a new capacity. */
  setCount: (count: number) => void
  /** Advance phase modulation + spike bookkeeping. Call once per frame. */
  tick: (deltaSec: number) => void
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

const TWO_PI = Math.PI * 2
const WORLD_SCALE = 200

export function createAmbientNeurons(initialCount: number = 30000): AmbientNeuronsHandle {
  const group = new THREE.Group()
  group.name = 'AmbientNeurons'

  // Mutable state captured in closure and rebuilt on setCount()
  let count = Math.max(0, Math.floor(initialCount))
  let mesh: THREE.InstancedMesh
  let colorArray: Float32Array
  let phases: Float32Array
  let phaseSpeeds: Float32Array
  let baseColors: Float32Array
  let assignments: Int32Array
  // Per-neuron transient spike intensity (multiplier on top of base brightness).
  // 1.0 = no spike, >1 during an active spike, decays back to 1.
  let spikeBoost: Float32Array
  let spikeTtl: Float32Array // seconds remaining on spike
  let frameCounter = 0

  // Scratch objects reused to avoid per-call allocations
  const scratchMatrix = new THREE.Matrix4()
  const scratchPosition = new THREE.Vector3()
  const scratchScale = new THREE.Vector3()
  const identityQuat = new THREE.Quaternion()
  const zeroVec = new THREE.Vector3(0, 0, 0)
  const zeroScale = new THREE.Vector3(0, 0, 0)
  const hiddenMatrix = new THREE.Matrix4().compose(zeroVec, identityQuat, zeroScale)

  const build = (capacity: number): void => {
    // Dispose the previous mesh (if any) to release GPU buffers.
    if (mesh) {
      group.remove(mesh)
      mesh.geometry.dispose()
      ;(mesh.material as THREE.Material).dispose()
    }

    count = Math.max(0, Math.floor(capacity))

    // Small low-poly sphere — instanced rendering amortizes the cost.
    const geometry = new THREE.SphereGeometry(0.4, 6, 6)
    const material = new THREE.MeshBasicMaterial({
      toneMapped: false,
      vertexColors: true,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
    })

    mesh = new THREE.InstancedMesh(geometry, material, count)
    mesh.frustumCulled = false
    mesh.count = count

    colorArray = new Float32Array(count * 3)
    mesh.instanceColor = new THREE.InstancedBufferAttribute(colorArray, 3)

    phases = new Float32Array(count)
    phaseSpeeds = new Float32Array(count)
    baseColors = new Float32Array(count * 3)
    assignments = new Int32Array(count)
    spikeBoost = new Float32Array(count)
    spikeTtl = new Float32Array(count)

    // Initialize everything hidden and neutral. setRegions() will populate.
    for (let i = 0; i < count; i++) {
      mesh.setMatrixAt(i, hiddenMatrix)
      assignments[i] = -1
      spikeBoost[i] = 1
      spikeTtl[i] = 0
    }
    mesh.instanceMatrix.needsUpdate = true
    mesh.instanceColor.needsUpdate = true

    group.add(mesh)
  }

  build(count)

  // -------------------------------------------------------------------------
  // Uniform point inside a unit sphere via rejection sampling.
  // -------------------------------------------------------------------------
  const sampleUnitSphere = (out: THREE.Vector3): void => {
    for (;;) {
      const x = Math.random() * 2 - 1
      const y = Math.random() * 2 - 1
      const z = Math.random() * 2 - 1
      if (x * x + y * y + z * z <= 1) {
        out.set(x, y, z)
        return
      }
    }
  }

  // -------------------------------------------------------------------------
  // setRegions — distribute the ambient population across the given regions.
  // -------------------------------------------------------------------------
  const setRegions = (regions: AmbientRegionInput[]): void => {
    if (count === 0) return

    if (regions.length === 0) {
      // No regions — hide everything.
      for (let i = 0; i < count; i++) {
        mesh.setMatrixAt(i, hiddenMatrix)
        baseColors[i * 3 + 0] = 0
        baseColors[i * 3 + 1] = 0
        baseColors[i * 3 + 2] = 0
        colorArray[i * 3 + 0] = 0
        colorArray[i * 3 + 1] = 0
        colorArray[i * 3 + 2] = 0
        assignments[i] = -1
      }
      mesh.instanceMatrix.needsUpdate = true
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
      return
    }

    // Precompute per-region THREE.Color to avoid re-parsing inside the hot loop.
    const regionColors: THREE.Color[] = regions.map((r) => new THREE.Color(r.color))
    const perRegion = Math.floor(count / regions.length)
    const remainder = count - perRegion * regions.length

    const offset = new THREE.Vector3()

    let i = 0
    for (let r = 0; r < regions.length; r++) {
      const region = regions[r]
      const rc = regionColors[r]
      // Spread the remainder across the first few regions so we use every slot.
      const take = perRegion + (r < remainder ? 1 : 0)
      const worldRadius = region.radius * WORLD_SCALE

      for (let k = 0; k < take; k++, i++) {
        // Uniform point in the region sphere.
        sampleUnitSphere(offset)
        scratchPosition.set(
          region.center.x + offset.x * worldRadius,
          region.center.y + offset.y * worldRadius,
          region.center.z + offset.z * worldRadius,
        )

        // Uniform scale in 0.5..1.5
        const s = 0.5 + Math.random()
        scratchScale.set(s, s, s)
        scratchMatrix.compose(scratchPosition, identityQuat, scratchScale)
        mesh.setMatrixAt(i, scratchMatrix)

        // Base color = region color * random 0.6..1.0 intensity
        const intensity = 0.6 + Math.random() * 0.4
        const br = rc.r * intensity
        const bg = rc.g * intensity
        const bb = rc.b * intensity
        const ci = i * 3
        baseColors[ci + 0] = br
        baseColors[ci + 1] = bg
        baseColors[ci + 2] = bb
        // Seed current color to base (tick() will modulate).
        colorArray[ci + 0] = br
        colorArray[ci + 1] = bg
        colorArray[ci + 2] = bb

        assignments[i] = r
        phases[i] = Math.random() * TWO_PI
        // Slow oscillation: 0.5..1.5 rad/sec
        phaseSpeeds[i] = 0.5 + Math.random()
        spikeBoost[i] = 1
        spikeTtl[i] = 0
      }
    }

    // Hide any leftover slots (shouldn't happen with the remainder distribution, but be safe).
    for (; i < count; i++) {
      mesh.setMatrixAt(i, hiddenMatrix)
      const ci = i * 3
      baseColors[ci + 0] = 0
      baseColors[ci + 1] = 0
      baseColors[ci + 2] = 0
      colorArray[ci + 0] = 0
      colorArray[ci + 1] = 0
      colorArray[ci + 2] = 0
      assignments[i] = -1
    }

    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  }

  // -------------------------------------------------------------------------
  // setCount — rebuild the mesh with a new capacity.
  // -------------------------------------------------------------------------
  const setCount = (n: number): void => {
    build(n)
  }

  // -------------------------------------------------------------------------
  // tick — advance phases + update instance colors (hot path).
  // -------------------------------------------------------------------------
  const tick = (deltaSec: number): void => {
    if (count === 0) return

    frameCounter++

    // Direct Float32Array manipulation — avoid per-neuron setColorAt() overhead.
    for (let i = 0; i < count; i++) {
      if (assignments[i] < 0) continue

      // Phase advance
      phases[i] += deltaSec * phaseSpeeds[i]
      if (phases[i] > TWO_PI) phases[i] -= TWO_PI

      // brightness = 0.3 + 0.5 * (sin(phase) * 0.5 + 0.5)
      //            = 0.3 + 0.25 * (sin(phase) + 1)
      //            = 0.55 + 0.25 * sin(phase)
      let brightness = 0.55 + 0.25 * Math.sin(phases[i])

      // Apply spike decay, if active.
      if (spikeTtl[i] > 0) {
        spikeTtl[i] -= deltaSec
        if (spikeTtl[i] <= 0) {
          spikeTtl[i] = 0
          spikeBoost[i] = 1
        }
        brightness *= spikeBoost[i]
      }

      const ci = i * 3
      colorArray[ci + 0] = baseColors[ci + 0] * brightness
      colorArray[ci + 1] = baseColors[ci + 1] * brightness
      colorArray[ci + 2] = baseColors[ci + 2] * brightness
    }

    // Every 60 frames: pick 50 random live neurons and spike them for 200ms.
    if (frameCounter % 60 === 0) {
      const spikeDuration = 0.2
      for (let s = 0; s < 50; s++) {
        const idx = (Math.random() * count) | 0
        if (assignments[idx] < 0) continue
        spikeBoost[idx] = 1.5
        spikeTtl[idx] = spikeDuration
      }
    }

    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true
    }
  }

  return {
    group,
    setRegions,
    setCount,
    tick,
  }
}
