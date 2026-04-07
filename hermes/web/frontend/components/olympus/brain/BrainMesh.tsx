/**
 * BrainMesh — Phase 43 sub-phase 1
 *
 * Loads an anatomically-styled brain mesh. Attempts to fetch a pre-baked
 * GLB from /olympus/brain/cortex.glb first; on any failure falls back to a
 * procedurally-generated icosahedron-based brain with hemispheres and a
 * brainstem stub. Also samples per-daemon region points on/near the surface
 * for downstream particle systems and HUD anchors.
 *
 * File scope (Phase 43 wave 1): OWN ONLY this file.
 */

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

export interface BrainMeshHandle {
  mesh: THREE.Object3D
  bounds: THREE.Box3
  regionPoints: Map<string, THREE.Vector3[]>
}

// Hand-coded anatomical centers (unit-sphere space, pre-scale).
// NOTE: BrainRegions module is being authored in parallel (sub-phase 5).
// We deliberately do NOT import it to avoid wave-1 coupling.
interface RegionSpec {
  center: THREE.Vector3
  radius: number
}

const REGION_CENTERS: Record<string, RegionSpec> = {
  hermes:     { center: new THREE.Vector3(-0.7,  0.0,  0.3), radius: 0.30 },
  perseus:    { center: new THREE.Vector3( 0.0, -0.7, -0.3), radius: 0.30 },
  titan:      { center: new THREE.Vector3( 0.0,  0.5,  0.7), radius: 0.40 },
  conway:     { center: new THREE.Vector3( 0.0, -0.2,  0.8), radius: 0.30 },
  ruflo:      { center: new THREE.Vector3( 0.0,  0.5,  0.0), radius: 0.35 },
  clawdbot:   { center: new THREE.Vector3( 0.0,  0.0, -0.8), radius: 0.30 },
  deerflow:   { center: new THREE.Vector3( 0.4, -0.2,  0.0), radius: 0.25 },
  openjarvis: { center: new THREE.Vector3( 0.0,  0.0,  0.0), radius: 0.20 },
}

const POINTS_PER_REGION = 500
const WORLD_SCALE = 200 // brain mesh is scaled to ~200 units in the scene

const BRAIN_COLOR = 0x4466aa

/** Material shared across every piece of the procedural brain. */
function createBrainMaterial(): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color: BRAIN_COLOR,
    opacity: 0.12,
    transparent: true,
    wireframe: true,
    depthWrite: false,
    toneMapped: true,
  })
}

/**
 * Two-octave noise displacement applied in-place to a BufferGeometry's
 * position attribute. Produces a gently gyrated, brain-like surface.
 */
function displaceGeometry(geometry: THREE.BufferGeometry): void {
  const pos = geometry.attributes.position as THREE.BufferAttribute
  const v = new THREE.Vector3()
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i)
    // Normalize so the noise rides a unit sphere regardless of radius.
    const len = v.length() || 1
    const nx = v.x / len
    const ny = v.y / len
    const nz = v.z / len

    const large = Math.sin(nx * 1.5) * Math.cos(ny * 1.5) * Math.sin(nz * 1.5) * 0.15
    const small = Math.sin(nx * 8.0) * Math.cos(ny * 8.0) * Math.sin(nz * 8.0) * 0.04
    const displacement = large + small

    const scale = 1 + displacement
    pos.setXYZ(i, nx * scale, ny * scale, nz * scale)
  }
  pos.needsUpdate = true
  geometry.computeVertexNormals()
  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
}

/** Make a mesh transparent to raycasts (clicks pass through). */
function disableRaycast(object: THREE.Object3D): void {
  object.raycast = () => {
    /* no-op: brain must not intercept pointer events */
  }
  object.children.forEach(disableRaycast)
}

/**
 * Build the procedural fallback brain: two gyrated hemispheres + brainstem,
 * all wrapped in a Group and returned ready to add to the scene.
 */
function buildProceduralBrain(): THREE.Group {
  const group = new THREE.Group()
  group.name = 'BrainMesh.procedural'

  // --- Left hemisphere ---
  const leftGeom = new THREE.IcosahedronGeometry(1, 4)
  displaceGeometry(leftGeom)
  const leftMat = createBrainMaterial()
  const left = new THREE.Mesh(leftGeom, leftMat)
  left.name = 'BrainMesh.hemi.left'
  left.position.x = -0.025 // half of 0.05 separation

  // --- Right hemisphere (mirrored) ---
  const rightGeom = leftGeom.clone()
  const rightMat = createBrainMaterial()
  const right = new THREE.Mesh(rightGeom, rightMat)
  right.name = 'BrainMesh.hemi.right'
  right.scale.x = -1
  right.position.x = 0.025

  // --- Brainstem stub ---
  const stemGeom = new THREE.CylinderGeometry(0.08, 0.12, 0.3, 12)
  const stemMat = createBrainMaterial()
  const stem = new THREE.Mesh(stemGeom, stemMat)
  stem.name = 'BrainMesh.brainstem'
  stem.position.set(0, -0.6, 0)

  group.add(left, right, stem)
  return group
}

/**
 * Attempt to load a pre-baked cortex GLB. Resolves null on any failure
 * (404, parse error, network) so callers can fall back cleanly.
 */
async function tryLoadCortexGlb(): Promise<THREE.Group | null> {
  const url = '/olympus/brain/cortex.glb'

  // Probe with fetch first so a missing file is a quiet null rather than
  // a noisy GLTFLoader parse error in the console.
  try {
    const probe = await fetch(url, { method: 'HEAD' })
    if (!probe.ok) return null
  } catch {
    return null
  }

  return new Promise<THREE.Group | null>((resolve) => {
    const loader = new GLTFLoader()
    loader.load(
      url,
      (gltf) => {
        const group = new THREE.Group()
        group.name = 'BrainMesh.gltf'
        group.add(gltf.scene)

        // Override materials to match the wireframe aesthetic.
        const mat = createBrainMaterial()
        group.traverse((obj) => {
          const mesh = obj as THREE.Mesh
          if ((mesh as THREE.Mesh).isMesh) {
            mesh.material = mat
          }
        })
        resolve(group)
      },
      undefined,
      () => resolve(null),
    )
  })
}

/**
 * Rejection-sample N points inside a sphere of the given radius centered
 * at `center`, all in unit-brain space. Points are then scaled by
 * WORLD_SCALE to live in final scene coordinates.
 */
function samplePointsInRegion(
  center: THREE.Vector3,
  radius: number,
  count: number,
): THREE.Vector3[] {
  const points: THREE.Vector3[] = []
  // Rejection sampling inside the unit cube, rescaled to `radius`.
  while (points.length < count) {
    const x = Math.random() * 2 - 1
    const y = Math.random() * 2 - 1
    const z = Math.random() * 2 - 1
    if (x * x + y * y + z * z > 1) continue
    const p = new THREE.Vector3(
      (center.x + x * radius) * WORLD_SCALE,
      (center.y + y * radius) * WORLD_SCALE,
      (center.z + z * radius) * WORLD_SCALE,
    )
    points.push(p)
  }
  return points
}

function sampleAllRegions(): Map<string, THREE.Vector3[]> {
  const out = new Map<string, THREE.Vector3[]>()
  for (const [daemon, spec] of Object.entries(REGION_CENTERS)) {
    out.set(daemon, samplePointsInRegion(spec.center, spec.radius, POINTS_PER_REGION))
  }
  return out
}

/**
 * Load the brain mesh. Tries the pre-baked GLB, falls back to procedural.
 * Always resolves — never rejects — so callers can render unconditionally.
 */
export async function loadBrainMesh(): Promise<BrainMeshHandle> {
  let group: THREE.Group | null = null

  try {
    group = await tryLoadCortexGlb()
  } catch {
    group = null
  }

  if (!group) {
    group = buildProceduralBrain()
  }

  // Click-through + no culling (brain is huge and always visible).
  group.frustumCulled = false
  group.traverse((obj) => {
    obj.frustumCulled = false
  })
  disableRaycast(group)

  const bounds = new THREE.Box3().setFromObject(group)
  const regionPoints = sampleAllRegions()

  return { mesh: group, bounds, regionPoints }
}
