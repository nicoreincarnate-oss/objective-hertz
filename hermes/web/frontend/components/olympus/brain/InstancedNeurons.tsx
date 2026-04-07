/**
 * InstancedNeurons — Phase 43 sub-phase 2
 *
 * High-performance neuron rendering via THREE.InstancedMesh with raycast picking.
 * Wraps a single low-poly sphere instanced up to `maxCount` slots. Unused slots
 * are hidden by zero-scaling their instance matrix.
 *
 * Pure module (no React) despite .tsx extension — kept for consistency with the
 * sibling brain modules in this directory.
 */

import * as THREE from 'three'

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface NeuronInput {
  id: string
  position: THREE.Vector3
  color: string
  /** Radius multiplier, expected range 0.5 – 3 */
  size: number
  /** HDR multiplier for emissive look, expected range 0 – 2 */
  intensity: number
}

export interface InstancedNeuronsHandle {
  /** Add this group to your THREE.Scene */
  group: THREE.Group
  /** Upload a new node set (clamped to maxCount). Hidden slots are zero-scaled. */
  setNodes: (nodes: NeuronInput[]) => void
  /** Override a single instance's color (e.g. hover/selection highlight) */
  highlightInstance: (instanceId: number, color: THREE.Color) => void
  /** Raycast against the instanced mesh; returns the closest hit's node id or null */
  pickInstance: (raycaster: THREE.Raycaster) => string | null
  /** Map an instance index back to its node id */
  nodeIdAtInstance: (instanceId: number) => string | null
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createInstancedNeurons(maxCount: number = 50000): InstancedNeuronsHandle {
  // Low-poly sphere is fine — instanced rendering amortizes the cost.
  const geometry = new THREE.SphereGeometry(1, 8, 8)

  const material = new THREE.MeshBasicMaterial({
    toneMapped: false,
    vertexColors: true,
  })

  const mesh = new THREE.InstancedMesh(geometry, material, maxCount)
  mesh.frustumCulled = false
  mesh.count = maxCount // keep full capacity addressable; hidden slots are zero-scaled

  // Per-instance color attribute (rgb float per slot)
  const colorArray = new Float32Array(maxCount * 3)
  mesh.instanceColor = new THREE.InstancedBufferAttribute(colorArray, 3)

  // Scratch objects — reused to avoid per-call allocations
  const scratchMatrix = new THREE.Matrix4()
  const scratchPosition = new THREE.Vector3()
  const scratchQuat = new THREE.Quaternion()
  const scratchScale = new THREE.Vector3()
  const scratchColor = new THREE.Color()
  const zeroScale = new THREE.Vector3(0, 0, 0)
  const identityQuat = new THREE.Quaternion()
  const originVec = new THREE.Vector3(0, 0, 0)

  // Initialize every slot to hidden (scale 0) + black color
  const hiddenMatrix = new THREE.Matrix4().compose(originVec, identityQuat, zeroScale)
  for (let i = 0; i < maxCount; i++) {
    mesh.setMatrixAt(i, hiddenMatrix)
    colorArray[i * 3 + 0] = 0
    colorArray[i * 3 + 1] = 0
    colorArray[i * 3 + 2] = 0
  }
  mesh.instanceMatrix.needsUpdate = true
  mesh.instanceColor.needsUpdate = true

  // Wrap in a group so consumers can transform/toggle the whole system
  const group = new THREE.Group()
  group.name = 'InstancedNeurons'
  group.add(mesh)

  // Bookkeeping
  const idToInstance = new Map<string, number>()
  const instanceToId = new Map<number, string>()
  let activeCount = 0

  // -------------------------------------------------------------------------
  // setNodes
  // -------------------------------------------------------------------------
  const setNodes = (nodes: NeuronInput[]): void => {
    const limit = Math.min(nodes.length, maxCount)

    idToInstance.clear()
    instanceToId.clear()

    for (let i = 0; i < limit; i++) {
      const node = nodes[i]

      // position + scale compose (uniform scale by node.size)
      scratchPosition.copy(node.position)
      scratchScale.set(node.size, node.size, node.size)
      scratchMatrix.compose(scratchPosition, identityQuat, scratchScale)
      mesh.setMatrixAt(i, scratchMatrix)

      // color * intensity (HDR multiplier — toneMapped:false preserves >1)
      scratchColor.set(node.color).multiplyScalar(node.intensity)
      colorArray[i * 3 + 0] = scratchColor.r
      colorArray[i * 3 + 1] = scratchColor.g
      colorArray[i * 3 + 2] = scratchColor.b

      idToInstance.set(node.id, i)
      instanceToId.set(i, node.id)
    }

    // Hide any slots beyond the new node count
    for (let i = limit; i < activeCount; i++) {
      mesh.setMatrixAt(i, hiddenMatrix)
      colorArray[i * 3 + 0] = 0
      colorArray[i * 3 + 1] = 0
      colorArray[i * 3 + 2] = 0
    }

    activeCount = limit

    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true
    }
  }

  // -------------------------------------------------------------------------
  // highlightInstance
  // -------------------------------------------------------------------------
  const highlightInstance = (instanceId: number, color: THREE.Color): void => {
    if (instanceId < 0 || instanceId >= maxCount) return
    colorArray[instanceId * 3 + 0] = color.r
    colorArray[instanceId * 3 + 1] = color.g
    colorArray[instanceId * 3 + 2] = color.b
    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true
    }
  }

  // -------------------------------------------------------------------------
  // pickInstance
  // -------------------------------------------------------------------------
  const pickInstance = (raycaster: THREE.Raycaster): string | null => {
    const hits = raycaster.intersectObject(mesh, false)
    if (hits.length === 0) return null

    // intersectObject returns sorted by distance — first is closest
    for (const hit of hits) {
      const instanceId = hit.instanceId
      if (instanceId === undefined || instanceId === null) continue
      const id = instanceToId.get(instanceId)
      if (id !== undefined) return id
    }
    return null
  }

  // -------------------------------------------------------------------------
  // nodeIdAtInstance
  // -------------------------------------------------------------------------
  const nodeIdAtInstance = (instanceId: number): string | null => {
    const id = instanceToId.get(instanceId)
    return id === undefined ? null : id
  }

  return {
    group,
    setNodes,
    highlightInstance,
    pickInstance,
    nodeIdAtInstance,
  }
}
