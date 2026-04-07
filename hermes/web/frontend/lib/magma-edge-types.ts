/**
 * Canonical MAGMA edge types — mirrors shared/magma_edge_types.py.
 * Source of truth for brain graph rendering in Memory3DGraph.tsx.
 */

export enum EdgeType {
  DERIVED_FROM = 'DERIVED_FROM',
  RELATED_TO = 'RELATED_TO',
  CONTRADICTS = 'CONTRADICTS',
  SUPPORTS = 'SUPPORTS',
  MENTIONS = 'MENTIONS',
  OWNED_BY = 'OWNED_BY',
  VERSION_OF = 'VERSION_OF',
  CAUSED_BY = 'CAUSED_BY',
}

export interface EdgeVisual {
  color: string
  opacity: number
  width: number
  dashes: boolean
}

export const EDGE_VISUAL: Record<EdgeType, EdgeVisual> = {
  [EdgeType.DERIVED_FROM]: { color: '#888888', opacity: 0.3, width: 1, dashes: false },
  [EdgeType.RELATED_TO]:   { color: '#88ddff', opacity: 0.5, width: 1.5, dashes: false },
  [EdgeType.CONTRADICTS]:  { color: '#ff5a7a', opacity: 0.7, width: 1.5, dashes: true },
  [EdgeType.SUPPORTS]:     { color: '#44ff88', opacity: 0.6, width: 2, dashes: false },
  [EdgeType.MENTIONS]:     { color: '#ffffff', opacity: 0.3, width: 0.8, dashes: false },
  [EdgeType.OWNED_BY]:     { color: '#ffd700', opacity: 0.4, width: 1, dashes: false },
  [EdgeType.VERSION_OF]:   { color: '#aa88ff', opacity: 0.7, width: 2, dashes: false },
  [EdgeType.CAUSED_BY]:    { color: '#ffb347', opacity: 0.6, width: 1.5, dashes: false },
}

export function visualForEdge(type: string | undefined): EdgeVisual {
  if (!type) return EDGE_VISUAL[EdgeType.RELATED_TO]
  const t = type as EdgeType
  return EDGE_VISUAL[t] ?? EDGE_VISUAL[EdgeType.RELATED_TO]
}
