import * as THREE from 'three'

/**
 * BrainRegions — Phase 43 sub-phase 5
 *
 * Hand-coded mapping of Objective Hertz daemons to anatomical brain regions.
 * Each region defines a spherical volume in normalized brain-space (roughly
 * a unit sphere centered at origin) into which particles / nodes belonging
 * to that daemon can be sampled.
 *
 * Coordinate convention (normalized, before `scale`):
 *   - x: left (-) / right (+)
 *   - y: inferior (-) / superior (+)
 *   - z: posterior (-) / anterior (+)
 *
 * All functions are pure — no side effects, no I/O.
 */

export interface BrainRegion {
  daemon: string
  name: string
  center: THREE.Vector3
  radius: number
  color: string
}

export const BRAIN_REGIONS: Record<string, BrainRegion> = {
  hermes: {
    daemon: 'hermes',
    name: 'Temporal Lobe',
    center: new THREE.Vector3(-0.7, 0, 0.3),
    radius: 0.3,
    color: '#44ff88',
  },
  perseus: {
    daemon: 'perseus',
    name: 'Cerebellum',
    center: new THREE.Vector3(0, -0.7, -0.3),
    radius: 0.3,
    color: '#88c8ff',
  },
  titan: {
    daemon: 'titan',
    name: 'Frontal Cortex',
    center: new THREE.Vector3(0, 0.5, 0.7),
    radius: 0.4,
    color: '#ff8844',
  },
  conway: {
    daemon: 'conway',
    name: 'Orbitofrontal Cortex',
    center: new THREE.Vector3(0, -0.2, 0.8),
    radius: 0.3,
    color: '#ffcc44',
  },
  ruflo: {
    daemon: 'ruflo',
    name: 'Motor Cortex',
    center: new THREE.Vector3(0, 0.5, 0),
    radius: 0.35,
    color: '#ff5a7a',
  },
  clawdbot: {
    daemon: 'clawdbot',
    name: 'Occipital Cortex',
    center: new THREE.Vector3(0, 0, -0.8),
    radius: 0.3,
    color: '#aa88ff',
  },
  deerflow: {
    daemon: 'deerflow',
    name: 'Hippocampus',
    center: new THREE.Vector3(0.4, -0.2, 0),
    radius: 0.25,
    color: '#ff44aa',
  },
  openjarvis: {
    daemon: 'openjarvis',
    name: 'Thalamus',
    center: new THREE.Vector3(0, 0, 0),
    radius: 0.2,
    color: '#ffd700',
  },
}

/**
 * Look up the brain region for a given daemon name.
 * Falls back to the `openjarvis` (Thalamus) region for unknown daemons,
 * since the thalamus is the central relay hub — a sensible default.
 */
export function regionForDaemon(daemon: string): BrainRegion {
  return BRAIN_REGIONS[daemon] ?? BRAIN_REGIONS.openjarvis
}

/**
 * Sample `count` points uniformly distributed inside the region's sphere.
 *
 * Uses rejection sampling in the unit cube [-1,1]^3: pick a random point,
 * reject if its length exceeds 1 (outside unit sphere), otherwise scale by
 * `region.radius`, translate by `region.center`, then multiply the final
 * position by `scale` to convert from normalized brain-space to world units.
 *
 * @param region  The brain region whose sphere we are sampling.
 * @param count   Number of points to return.
 * @param scale   World-space scale factor applied after placement. Default 200.
 */
export function sampleInRegion(
  region: BrainRegion,
  count: number,
  scale: number = 200,
): THREE.Vector3[] {
  const points: THREE.Vector3[] = []
  while (points.length < count) {
    const x = Math.random() * 2 - 1
    const y = Math.random() * 2 - 1
    const z = Math.random() * 2 - 1
    if (x * x + y * y + z * z > 1) continue
    const point = new THREE.Vector3(
      (x * region.radius + region.center.x) * scale,
      (y * region.radius + region.center.y) * scale,
      (z * region.radius + region.center.z) * scale,
    )
    points.push(point)
  }
  return points
}

/**
 * All brain regions as an array, in declaration order.
 */
export function allRegions(): BrainRegion[] {
  return Object.values(BRAIN_REGIONS)
}
