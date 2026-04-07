'use client'

/**
 * Memory3DGraph — The brain.
 *
 * Uses the real `threejs-brain-animation` npm package (by Bytez, MIT licensed)
 * — https://github.com/bytezpro/threejs-brain-animation
 * — https://www.npmjs.com/package/threejs-brain-animation
 *
 * This is a published React component that renders an actual neural-network
 * brain animation with constantly firing neurons, particle flow along
 * synaptic connections, and built-in camera controls. We are NOT
 * reimplementing what's already done well by an open-source library.
 *
 * Stats overlay + memory detail drawer are layered ON TOP of the published
 * component. Real MAGMA data (when the API is reachable) drives a side-panel
 * memory list — clicking opens the existing MemoryDetailDrawer.
 */

import dynamic from 'next/dynamic'
import { useEffect, useState, useCallback } from 'react'
import { MemoryDetailDrawer } from './MemoryDetailDrawer'
import 'threejs-brain-animation/dist/main.css'

// The package touches `window` at module-load (Three.js renderer init), so we
// must dynamic-import it with SSR disabled. Next.js 16 / React 19 friendly.
const Brain = dynamic(
  () => import('threejs-brain-animation').then((mod) => mod.Brain),
  { ssr: false, loading: () => <Memory3DGraphLoading /> },
)

// ─── Loading state ─────────────────────────────────────────────────────────

function Memory3DGraphLoading() {
  return (
    <div className="w-full h-full flex items-center justify-center bg-[#04080c]">
      <div className="text-cyan-300/70 text-xs tracking-widest font-mono animate-pulse">
        SUMMONING THE POOL...
      </div>
    </div>
  )
}

// ─── API types ─────────────────────────────────────────────────────────────

interface ServerNode {
  id: string
  label?: string
  group?: string
  type?: string
  confidence?: number
  size?: number
  daemon?: string
  color?: string
}

interface ServerEdge {
  from: string
  to: string
  type?: string
}

interface ServerGraphData {
  nodes: ServerNode[]
  edges: ServerEdge[]
}

// ─── Daemon palette (shared with the rest of Olympus) ──────────────────────

const DAEMON_COLORS: Record<string, string> = {
  hermes: '#44ff88',
  perseus: '#88c8ff',
  titan: '#ff8844',
  clawdbot: '#aa88ff',
  conway: '#ffcc44',
  ruflo: '#ff5a7a',
  openjarvis: '#ffd700',
  memory: '#44ffee',
  unknown: '#88ddff',
}

function colorForNode(n: ServerNode): string {
  if (n.color) return n.color
  const daemon = (n.daemon ?? n.group ?? 'unknown').toLowerCase()
  return DAEMON_COLORS[daemon] ?? DAEMON_COLORS.unknown
}

// ─── Demo data (when MAGMA is offline) ─────────────────────────────────────

function makeFakeMemoryList(): ServerNode[] {
  const daemons = ['hermes', 'perseus', 'titan', 'clawdbot', 'conway', 'ruflo', 'memory', 'openjarvis']
  return Array.from({ length: 20 }, (_, i) => ({
    id: `demo_${i}`,
    label: `Demo Memory ${i + 1}`,
    daemon: daemons[i % daemons.length],
    type: 'demo',
    confidence: 0.4 + Math.random() * 0.6,
  }))
}

// ─── Main component ────────────────────────────────────────────────────────

interface Memory3DGraphProps {
  onSelect?: (id: string) => void
  pollInterval?: number
  debugFakeData?: boolean
}

export function Memory3DGraph({
  onSelect,
  pollInterval = 5000,
  debugFakeData = false,
}: Memory3DGraphProps) {
  const [memories, setMemories] = useState<ServerNode[]>([])
  const [stats, setStats] = useState({ neurons: 0, synapses: 0, lastUpdate: 0 })
  const [selectedMemoryId, setSelectedMemoryId] = useState<string | null>(null)
  const [fallbackActive, setFallbackActive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ingest = useCallback((data: ServerGraphData) => {
    setMemories(data.nodes)
    setStats({
      neurons: data.nodes.length,
      synapses: data.edges?.length ?? 0,
      lastUpdate: Date.now(),
    })
  }, [])

  const loadDemoGraph = useCallback(() => {
    const fake = makeFakeMemoryList()
    ingest({ nodes: fake, edges: [] })
    setFallbackActive(true)
    setError(null)
  }, [ingest])

  const fetchGraph = useCallback(async () => {
    if (debugFakeData) {
      loadDemoGraph()
      return
    }
    try {
      const res = await fetch('/api/memory/graph?depth=3&limit=500', {
        cache: 'no-store',
      })
      if (!res.ok) {
        loadDemoGraph()
        return
      }
      const incoming = (await res.json()) as ServerGraphData
      if (!incoming?.nodes || incoming.nodes.length === 0) {
        loadDemoGraph()
        return
      }
      setFallbackActive(false)
      setError(null)
      ingest(incoming)
    } catch {
      loadDemoGraph()
    }
  }, [debugFakeData, ingest, loadDemoGraph])

  useEffect(() => {
    fetchGraph()
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      fetchGraph()
    }, pollInterval)
    return () => clearInterval(interval)
  }, [fetchGraph, pollInterval])

  const handleMemoryClick = useCallback(
    (id: string) => {
      setSelectedMemoryId(id)
      onSelect?.(id)
    },
    [onSelect],
  )

  // Add the model-loaded class to <html> so the package's CSS reveals the canvas
  // (the package transitions opacity from 0 to 1 when html.model-loaded is set)
  useEffect(() => {
    const t = setTimeout(() => {
      if (typeof document !== 'undefined') {
        document.documentElement.classList.add('model-loaded')
      }
    }, 1500)
    return () => clearTimeout(t)
  }, [])

  return (
    <div className="relative w-full h-full bg-[#04080c] overflow-hidden">
      {/* The actual brain — published npm package (threejs-brain-animation by Bytez) */}
      {/* The package needs an #app container so its CSS rules apply */}
      <div id="app" className="absolute inset-0">
        <Brain />
      </div>

      {/* HUD overlay — top left */}
      <div className="absolute top-3 left-3 px-4 py-2.5 rounded border border-cyan-400/40 bg-black/70 backdrop-blur-md text-cyan-100 text-[11px] font-mono space-y-0.5 pointer-events-none shadow-[0_0_20px_rgba(68,255,238,0.2)] z-10">
        <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase mb-1">
          The Pool of Memory
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">NEURONS</span>
          <span className="text-cyan-100 font-bold">{stats.neurons}</span>
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">SYNAPSES</span>
          <span className="text-cyan-100 font-bold">{stats.synapses}</span>
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">LIVE SYNC</span>
          <span className="text-emerald-300 animate-pulse">●</span>
        </div>
        {(debugFakeData || fallbackActive) && (
          <div className="pt-1 border-t border-cyan-400/20 mt-1 text-amber-200/70 text-[9px] tracking-widest">
            {debugFakeData ? 'DEBUG MODE' : 'DEMO MODE'}
          </div>
        )}
        {fallbackActive && !debugFakeData && (
          <div className="text-amber-200/40 text-[8px] mt-0.5">
            magma offline · synthetic graph
          </div>
        )}
      </div>

      {/* Memory list — right side, scrollable, click to open */}
      {memories.length > 0 && (
        <div className="absolute top-3 right-3 bottom-3 w-72 z-10 pointer-events-auto">
          <div className="h-full rounded border border-cyan-400/30 bg-black/70 backdrop-blur-md overflow-hidden flex flex-col shadow-[0_0_20px_rgba(68,255,238,0.15)]">
            <div className="px-3 py-2 border-b border-cyan-400/20">
              <div className="text-[9px] text-cyan-300/60 tracking-[0.25em] uppercase">
                Memories
              </div>
              <div className="text-cyan-100 text-xs font-mono mt-0.5">
                {memories.length} active
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
              {memories.map((m) => {
                const color = colorForNode(m)
                return (
                  <button
                    key={m.id}
                    onClick={() => handleMemoryClick(m.id)}
                    className="w-full text-left px-2 py-1.5 rounded hover:bg-white/5 transition-colors group flex items-center gap-2"
                    style={{ cursor: 'pointer' }}
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0 shadow-[0_0_6px_currentColor]"
                      style={{ backgroundColor: color, color }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-cyan-100 text-[11px] font-mono truncate group-hover:text-cyan-50">
                        {m.label ?? m.id}
                      </div>
                      <div className="text-cyan-400/40 text-[9px] truncate">
                        {(m.daemon ?? m.group ?? 'unknown').toLowerCase()}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="absolute bottom-3 left-3 px-3 py-2 rounded border border-amber-400/40 bg-black/70 text-amber-200 text-[10px] font-mono pointer-events-none z-10">
          ⚠ {error} — the pool is dry
        </div>
      )}

      <div className="absolute bottom-3 right-3 text-[9px] text-cyan-300/30 font-mono tracking-widest pointer-events-none z-10">
        DRAG · ORBIT · CLICK A MEMORY
      </div>

      <MemoryDetailDrawer
        memoryId={selectedMemoryId}
        onClose={() => setSelectedMemoryId(null)}
      />
    </div>
  )
}
