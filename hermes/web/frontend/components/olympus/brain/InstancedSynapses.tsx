/**
 * Phase 43 sub-phase 3 — InstancedSynapses
 *
 * GPU-instanced synapse edges rendered as thin cylinders with a GLSL
 * Gaussian-pulse shader simulating action potentials traveling from the
 * presynaptic end (vU=0) to the postsynaptic end (vU=1).
 *
 * File scope: this file only. No external state, no new packages.
 */

import * as THREE from 'three'

export interface SynapseInput {
  from: THREE.Vector3
  to: THREE.Vector3
  color?: string
  intensity?: number
}

export interface InstancedSynapsesHandle {
  group: THREE.Group
  setEdges: (edges: SynapseInput[]) => void
  firePulse: (edgeIndex: number, durationMs?: number) => void
  tick: (deltaSec: number) => void
}

interface ActivePulse {
  startTime: number
  durationMs: number
}

const DEFAULT_MAX = 50000
const PULSE_OFF = 1.0 // Any value >= 1 places the Gaussian band past the tip → invisible.
const DEFAULT_COLOR = new THREE.Color(0x5ad7ff) // subtle cyan

const vertexShader = /* glsl */ `
  attribute float instanceProgress;
  attribute vec3 instanceTint;

  varying float vU;
  varying float vProgress;
  varying vec3 vColor;

  void main() {
    vU = uv.y;
    vProgress = instanceProgress;
    vColor = instanceTint;

    vec4 mvPosition = modelViewMatrix * instanceMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mvPosition;
  }
`

const fragmentShader = /* glsl */ `
  precision highp float;

  varying float vU;
  varying float vProgress;
  varying vec3 vColor;

  void main() {
    float baseAlpha = 0.18;
    // Gaussian pulse band centered at vProgress.
    float dist = vU - vProgress;
    float pulse = exp(-dist * dist * 60.0);

    // When progress is "off" (>= 1.0) the band is clamped off the tip so
    // the synapse fades to its base glow only.
    float offMask = step(vProgress, 0.999);
    pulse *= offMask;

    float alpha = baseAlpha + pulse * 0.9;
    vec3 finalColor = vColor + vec3(pulse) * 1.5;

    gl_FragColor = vec4(finalColor, alpha);
  }
`

export function createInstancedSynapses(
  maxCount: number = DEFAULT_MAX,
): InstancedSynapsesHandle {
  const group = new THREE.Group()
  group.name = 'InstancedSynapses'

  // Thin cylinder aligned along Y; open-ended, 6 radial segments (cheap).
  const geometry = new THREE.CylinderGeometry(0.05, 0.05, 1, 6, 1, true)

  // Per-instance progress attribute (float).
  const progressArray = new Float32Array(maxCount)
  progressArray.fill(PULSE_OFF)
  const progressAttr = new THREE.InstancedBufferAttribute(progressArray, 1)
  progressAttr.setUsage(THREE.DynamicDrawUsage)
  geometry.setAttribute('instanceProgress', progressAttr)

  // Per-instance tint attribute (vec3). We use this instead of the default
  // InstancedMesh color buffer so it flows through our custom shader cleanly.
  const tintArray = new Float32Array(maxCount * 3)
  for (let i = 0; i < maxCount; i++) {
    tintArray[i * 3 + 0] = DEFAULT_COLOR.r
    tintArray[i * 3 + 1] = DEFAULT_COLOR.g
    tintArray[i * 3 + 2] = DEFAULT_COLOR.b
  }
  const tintAttr = new THREE.InstancedBufferAttribute(tintArray, 3)
  tintAttr.setUsage(THREE.DynamicDrawUsage)
  geometry.setAttribute('instanceTint', tintAttr)

  const material = new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    toneMapped: false,
  })

  const mesh = new THREE.InstancedMesh(geometry, material, maxCount)
  mesh.frustumCulled = false
  mesh.count = 0 // no visible edges until setEdges is called
  group.add(mesh)

  // --- internal mutable state ---------------------------------------------
  const activePulses = new Map<number, ActivePulse>()
  let edgeCount = 0

  // Reusable scratch objects for setEdges math (avoid per-edge allocations).
  const scratchMid = new THREE.Vector3()
  const scratchDir = new THREE.Vector3()
  const scratchQuat = new THREE.Quaternion()
  const scratchScale = new THREE.Vector3()
  const scratchMatrix = new THREE.Matrix4()
  const scratchColor = new THREE.Color()
  const UP = new THREE.Vector3(0, 1, 0)

  function setEdges(edges: SynapseInput[]): void {
    const count = Math.min(edges.length, maxCount)
    edgeCount = count
    mesh.count = count
    activePulses.clear()

    for (let i = 0; i < count; i++) {
      const edge = edges[i]
      // Midpoint
      scratchMid.addVectors(edge.from, edge.to).multiplyScalar(0.5)
      // Direction and length
      scratchDir.subVectors(edge.to, edge.from)
      const length = scratchDir.length()
      if (length > 1e-6) {
        scratchDir.divideScalar(length)
      } else {
        scratchDir.copy(UP)
      }
      // Rotate base Y axis onto the edge direction.
      scratchQuat.setFromUnitVectors(UP, scratchDir)
      // Cylinder has unit height along Y → scale Y by length. X/Z keep 1.
      const intensity = edge.intensity ?? 1.0
      const radialScale = Math.max(0.25, Math.min(2.5, intensity))
      scratchScale.set(radialScale, length, radialScale)
      scratchMatrix.compose(scratchMid, scratchQuat, scratchScale)
      mesh.setMatrixAt(i, scratchMatrix)

      // Color per instance.
      if (edge.color) {
        scratchColor.set(edge.color)
      } else {
        scratchColor.copy(DEFAULT_COLOR)
      }
      tintArray[i * 3 + 0] = scratchColor.r
      tintArray[i * 3 + 1] = scratchColor.g
      tintArray[i * 3 + 2] = scratchColor.b

      // Initialize to "off".
      progressArray[i] = PULSE_OFF
    }

    mesh.instanceMatrix.needsUpdate = true
    tintAttr.needsUpdate = true
    progressAttr.needsUpdate = true
  }

  function firePulse(edgeIndex: number, durationMs: number = 500): void {
    if (edgeIndex < 0 || edgeIndex >= edgeCount) return
    activePulses.set(edgeIndex, {
      startTime: performance.now(),
      durationMs: Math.max(1, durationMs),
    })
    progressArray[edgeIndex] = 0
    progressAttr.needsUpdate = true
  }

  function tick(_deltaSec: number): void {
    if (activePulses.size === 0) return
    const now = performance.now()
    const finished: number[] = []

    activePulses.forEach((pulse, idx) => {
      const t = (now - pulse.startTime) / pulse.durationMs
      if (t >= 1) {
        progressArray[idx] = PULSE_OFF
        finished.push(idx)
      } else {
        progressArray[idx] = t
      }
    })

    for (const idx of finished) {
      activePulses.delete(idx)
    }
    progressAttr.needsUpdate = true
  }

  return {
    group,
    setEdges,
    firePulse,
    tick,
  }
}
