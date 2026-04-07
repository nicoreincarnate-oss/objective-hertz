/**
 * ActionPotential — pulse cascade engine for the anatomical brain.
 *
 * Phase 43 sub-phase 6. Pure logic, no THREE imports, fully testable.
 *
 * Responsibilities:
 * - Maintain adjacency: nodeId → list of edge indices originating from that node
 * - Fire synaptic pulses via a registered callback when a node "fires"
 * - Prevent infinite loops via a fired-node set that clears after idle
 * - Support delayed cascades through a pending-fire queue processed in tick()
 *
 * Limitation: because adjacency is stored as edge indices (not target node IDs),
 * multi-hop cascades from this engine alone can only decrement depth; the caller
 * must schedule downstream node fires using its own graph knowledge. For the
 * default wiring (Memory3DGraph.fireFromNode on new memories) a single-hop
 * pulse is sufficient and matches InstancedSynapses.firePulse semantics.
 */

export interface ActionPotentialEngine {
  fireFromNode: (nodeId: string, depth?: number) => void
  fireBurst: (nodeIds: string[]) => void
  registerSynapses: (edgesByNode: Map<string, number[]>) => void
  registerCallback: (cb: (edgeIndex: number) => void) => void
  tick: (deltaSec: number) => void
}

interface PendingFire {
  nodeId: string
  depth: number
  fireAt: number
}

const CASCADE_DELAY_MS = 200
const IDLE_RESET_SEC = 5

export function createActionPotentialEngine(): ActionPotentialEngine {
  let edgesByNode: Map<string, number[]> = new Map()
  const firedNodes: Set<string> = new Set()
  const pendingFires: PendingFire[] = []
  let pulseCallback: (edgeIndex: number) => void = () => {}
  let idleSec = 0

  const fireFromNode = (nodeId: string, depth: number = 2): void => {
    if (firedNodes.has(nodeId)) {
      return
    }
    firedNodes.add(nodeId)
    idleSec = 0

    const edges = edgesByNode.get(nodeId)
    if (!edges || edges.length === 0) {
      return
    }

    for (let i = 0; i < edges.length; i++) {
      try {
        pulseCallback(edges[i])
      } catch {
        // swallow callback errors so one bad pulse does not break the cascade
      }
    }

    if (depth > 0) {
      const fireAt = Date.now() + CASCADE_DELAY_MS
      pendingFires.push({ nodeId, depth: depth - 1, fireAt })
    }
  }

  const fireBurst = (nodeIds: string[]): void => {
    for (let i = 0; i < nodeIds.length; i++) {
      fireFromNode(nodeIds[i])
    }
  }

  const registerSynapses = (map: Map<string, number[]>): void => {
    edgesByNode = map
  }

  const registerCallback = (cb: (edgeIndex: number) => void): void => {
    pulseCallback = cb
  }

  const tick = (deltaSec: number): void => {
    const now = Date.now()

    if (pendingFires.length > 0) {
      idleSec = 0
      let write = 0
      for (let read = 0; read < pendingFires.length; read++) {
        const entry = pendingFires[read]
        if (entry.fireAt <= now) {
          // Allow the same node to fire again on the delayed pass by briefly
          // removing it from firedNodes, so adjacent edges can re-pulse.
          firedNodes.delete(entry.nodeId)
          fireFromNode(entry.nodeId, entry.depth)
        } else {
          pendingFires[write++] = entry
        }
      }
      pendingFires.length = write
    } else {
      idleSec += deltaSec
      if (idleSec >= IDLE_RESET_SEC && firedNodes.size > 0) {
        firedNodes.clear()
        idleSec = 0
      }
    }
  }

  return {
    fireFromNode,
    fireBurst,
    registerSynapses,
    registerCallback,
    tick,
  }
}
