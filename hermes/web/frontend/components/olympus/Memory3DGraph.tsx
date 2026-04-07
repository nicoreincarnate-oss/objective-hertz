'use client'

/**
 * Memory3DGraph — The brain. Real Obsidian-beating 3D force-directed memory graph.
 *
 * Architecture:
 *   base          = react-force-graph-3d (vasturiano) — battle-tested physics + scene
 *   rendering     = Three.js HDR emissive materials    — colors > 1.0 trigger bloom
 *   postprocess   = UnrealBloomPass injected into the lib's internal EffectComposer
 *   backdrop      = procedural starfield + wireframe brain-icosahedron
 *   camera        = OrbitControls w/ autoRotate + damping (cinematic)
 *   data          = /api/memory/graph polled every 5s — new nodes blast gold for 3s
 *
 * Every neuron is:
 *   - an HDR emissive core that bloom picks up as "glowing"
 *   - a pure-white inner pulse for an extra-bright center
 *   - a soft colored halo sphere
 *   - new neurons ADD a massive gold birth halo that fades over 3 seconds
 *     (the birth animation is driven by an external animation loop, NOT
 *      the one-shot nodeThreeObject callback, because the library doesn't
 *      re-invoke the callback per frame)
 *
 * Every synapse is:
 *   - a thin line (native force-graph links)
 *   - 4 directional particles per link that constantly flow source→target
 *   - cyan-white color that blooms
 *
 * Debug mode: append ?debug=graph to the URL to bypass the API and load
 * hardcoded fake data (20 random nodes) for visual iteration without the
 * backend running.
 */

import dynamic from 'next/dynamic'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import * as THREE from 'three'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { MemoryDetailDrawer } from './MemoryDetailDrawer'

const ForceGraph3D = dynamic(
  () => import('react-force-graph-3d').then((m) => m.default),
  { ssr: false, loading: () => <Memory3DGraphLoading /> },
)

// ─── Types ─────────────────────────────────────────────────────────────────

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
  weight?: number
  color?: { color?: string } | string
}

interface ServerGraphData {
  nodes: ServerNode[]
  edges: ServerEdge[]
  daemon_colors?: Record<string, string>
}

interface GraphNode {
  id: string
  name: string
  daemon: string
  type: string
  confidence: number
  val: number
  color: string
  bornAt: number
  isNew?: boolean
  /** Attached at render time — the burst mesh to fade over 3s */
  __burstMesh?: THREE.Mesh
  /** Attached at render time — the birth window deadline */
  __burstEnd?: number
}

interface GraphLink {
  source: string
  target: string
  color: string
  type?: string
}

interface GraphState {
  nodes: GraphNode[]
  links: GraphLink[]
}

// ─── Daemon palette — the colors of the room art ───────────────────────────

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

// ─── Debug mode: fake data for visual iteration ────────────────────────────

function makeFakeData(): ServerGraphData {
  const daemons = ['hermes', 'perseus', 'titan', 'clawdbot', 'conway', 'ruflo', 'memory']
  const nodes: ServerNode[] = Array.from({ length: 20 }, (_, i) => {
    const daemon = daemons[i % daemons.length]
    return {
      id: `fake_${i}`,
      label: `Memory ${i}`,
      daemon,
      group: daemon,
      type: 'test',
      confidence: 0.4 + Math.random() * 0.6,
      size: 6 + Math.random() * 8,
    }
  })
  const edges: ServerEdge[] = []
  for (let i = 0; i < nodes.length; i++) {
    const n = 1 + Math.floor(Math.random() * 3)
    for (let j = 0; j < n; j++) {
      const target = Math.floor(Math.random() * nodes.length)
      if (target !== i) {
        edges.push({ from: nodes[i].id, to: nodes[target].id, type: 'LINK', weight: 1 })
      }
    }
  }
  return { nodes, edges }
}

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

// ─── Main component ────────────────────────────────────────────────────────

interface Memory3DGraphProps {
  onSelect?: (id: string) => void
  /** Polling interval in ms. Defaults to 5s — matches WebSocket sync cadence. */
  pollInterval?: number
  /** When true, loads fake data instead of calling /api/memory/graph. */
  debugFakeData?: boolean
}

const BIRTH_WINDOW_MS = 3000

export function Memory3DGraph({
  onSelect,
  pollInterval = 5000,
  debugFakeData = false,
}: Memory3DGraphProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const sceneEnhancedRef = useRef(false)
  const brainRef = useRef<THREE.Mesh | null>(null)
  const rotatingObjectsRef = useRef<THREE.Object3D[]>([])
  const birthAnimationRafRef = useRef<number>(0)

  const [data, setData] = useState<GraphState>({ nodes: [], links: [] })
  const [stats, setStats] = useState({ nodes: 0, edges: 0, lastUpdate: 0, newInLast: 0 })
  const [error, setError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 600, h: 600 })
  const [selectedMemoryId, setSelectedMemoryId] = useState<string | null>(null)

  const knownIdsRef = useRef<Set<string>>(new Set())
  // All currently active birth-animating nodes (fades controlled by rAF loop)
  const activeBirthsRef = useRef<Map<string, GraphNode>>(new Map())

  // ─── Resize observer ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return
    const update = () => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      setSize({ w: Math.max(200, rect.width), h: Math.max(200, rect.height) })
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // ─── Data source (real API or fake data) ─────────────────────────────────
  const ingestGraphData = useCallback((incoming: ServerGraphData) => {
    const known = knownIdsRef.current
    const now = Date.now()
    let newCount = 0

    const nextNodes: GraphNode[] = incoming.nodes.map((n) => {
      const isNew = !known.has(n.id)
      if (isNew) newCount++
      return {
        id: n.id,
        name: n.label ?? n.id,
        daemon: (n.daemon ?? n.group ?? 'unknown').toLowerCase(),
        type: n.type ?? 'unknown',
        confidence: n.confidence ?? 0.5,
        val: Math.max(2, (n.size ?? 8) * 0.8),
        color: colorForNode(n),
        bornAt: now,
        isNew,
      }
    })

    for (const n of nextNodes) known.add(n.id)

    const nextLinks: GraphLink[] = (incoming.edges ?? []).map((e) => ({
      source: e.from,
      target: e.to,
      color:
        typeof e.color === 'string'
          ? e.color
          : e.color?.color ?? 'rgba(140, 200, 255, 0.35)',
      type: e.type,
    }))

    setData({ nodes: nextNodes, links: nextLinks })
    setStats({
      nodes: nextNodes.length,
      edges: nextLinks.length,
      lastUpdate: now,
      newInLast: newCount,
    })
  }, [])

  const fetchGraph = useCallback(async () => {
    if (debugFakeData) {
      ingestGraphData(makeFakeData())
      setError(null)
      return
    }
    try {
      const res = await fetch('/api/memory/graph?depth=3&limit=500', { cache: 'no-store' })
      if (!res.ok) {
        setError(`API ${res.status}`)
        return
      }
      const incoming = (await res.json()) as ServerGraphData
      if (!incoming?.nodes) {
        setError('No memory data')
        return
      }
      setError(null)
      ingestGraphData(incoming)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Network error')
    }
  }, [debugFakeData, ingestGraphData])

  useEffect(() => {
    fetchGraph()
    // Don't poll when the tab is hidden — saves CPU/bandwidth
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      fetchGraph()
    }, pollInterval)
    return () => clearInterval(interval)
  }, [fetchGraph, pollInterval])

  // ─── Scene enhancement: bloom, tone mapping, starfield, brain, auto-orbit ─
  useEffect(() => {
    if (sceneEnhancedRef.current) return

    let raf = 0
    const setup = () => {
      type EffectComposerLike = {
        passes: unknown[]
        addPass: (pass: unknown) => void
      }
      type ForceGraphInternal = {
        postProcessingComposer?: () => EffectComposerLike
        scene?: () => THREE.Scene
        renderer?: () => THREE.WebGLRenderer
        controls?: () => {
          autoRotate: boolean
          autoRotateSpeed: number
          enableDamping: boolean
          dampingFactor: number
        }
      }
      const fg = fgRef.current as ForceGraphInternal | null
      if (!fg?.postProcessingComposer || !fg?.scene || !fg?.renderer || !fg?.controls) {
        raf = requestAnimationFrame(setup)
        return
      }

      const composer = fg.postProcessingComposer()
      const scene = fg.scene()
      const renderer = fg.renderer()
      const controls = fg.controls()

      if (!composer || !scene || !renderer || !controls) {
        raf = requestAnimationFrame(setup)
        return
      }

      // ── 1. HDR tone mapping — unlocks bloom for colors > 1.0 ─────────────
      renderer.toneMapping = THREE.ACESFilmicToneMapping
      renderer.toneMappingExposure = 1.4
      renderer.outputColorSpace = THREE.SRGBColorSpace

      // ── 2. Unreal Bloom — the glow that makes neurons look alive ─────────
      // First verify the composer's current pass list so we insert bloom
      // in the right spot (after the RenderPass, before any OutputPass).
      if (process.env.NODE_ENV === 'development') {
        // eslint-disable-next-line no-console
        console.debug(
          '[Memory3DGraph] composer.passes before bloom injection:',
          composer.passes.length,
          composer.passes.map((p) => (p as { constructor: { name: string } }).constructor.name),
        )
      }

      const bloom = new UnrealBloomPass(
        new THREE.Vector2(size.w, size.h),
        1.6, // strength
        0.85, // radius
        0.0, // threshold — pick up EVERYTHING bright
      )
      composer.addPass(bloom)
      composer.addPass(new OutputPass())

      if (process.env.NODE_ENV === 'development') {
        // eslint-disable-next-line no-console
        console.debug(
          '[Memory3DGraph] composer.passes after bloom injection:',
          composer.passes.length,
          composer.passes.map((p) => (p as { constructor: { name: string } }).constructor.name),
        )
      }

      // ── 3. Auto-rotate orbit camera ──────────────────────────────────────
      controls.autoRotate = true
      controls.autoRotateSpeed = 0.35
      controls.enableDamping = true
      controls.dampingFactor = 0.08

      // ── 4. Starfield backdrop (2000 points in a sphere) ──────────────────
      const starGeom = new THREE.BufferGeometry()
      const starPos: number[] = []
      for (let i = 0; i < 2000; i++) {
        const r = 1400 + Math.random() * 600
        const theta = Math.random() * Math.PI * 2
        const phi = Math.acos(Math.random() * 2 - 1)
        starPos.push(
          r * Math.sin(phi) * Math.cos(theta),
          r * Math.sin(phi) * Math.sin(theta),
          r * Math.cos(phi),
        )
      }
      starGeom.setAttribute('position', new THREE.Float32BufferAttribute(starPos, 3))
      const starMat = new THREE.PointsMaterial({
        color: new THREE.Color(0x88aaff).multiplyScalar(2.5),
        size: 2.5,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.7,
        fog: false,
        toneMapped: false,
      })
      const stars = new THREE.Points(starGeom, starMat)
      stars.name = 'starfield'
      stars.frustumCulled = false
      scene.add(stars)

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

      // ── 6. Subtle rim light via a point light ────────────────────────────
      const rimLight = new THREE.PointLight(0x88c8ff, 2, 2000, 1)
      rimLight.position.set(500, 300, 400)
      scene.add(rimLight)

      // Background fog for depth
      scene.fog = new THREE.Fog(0x04080c, 800, 2500)
      scene.background = new THREE.Color(0x04080c)

      sceneEnhancedRef.current = true
    }

    raf = requestAnimationFrame(setup)
    return () => cancelAnimationFrame(raf)
  }, [size.w, size.h])

  // ─── Birth animation loop ────────────────────────────────────────────────
  // react-force-graph-3d calls `nodeThreeObject` ONCE per node creation, not
  // per frame. To animate the gold birth halo fading over 3 seconds we need
  // an external rAF loop that mutates the halo material references directly.
  useEffect(() => {
    const tick = () => {
      const now = Date.now()
      const active = activeBirthsRef.current
      const expired: string[] = []

      active.forEach((node, id) => {
        const mesh = node.__burstMesh
        const end = node.__burstEnd ?? 0
        if (!mesh || !mesh.material) {
          expired.push(id)
          return
        }
        const remaining = Math.max(0, end - now)
        if (remaining <= 0) {
          // Dispose the burst mesh to avoid memory leaks
          const mat = mesh.material as THREE.MeshBasicMaterial
          mesh.removeFromParent()
          mesh.geometry.dispose()
          mat.dispose()
          node.__burstMesh = undefined
          node.__burstEnd = undefined
          expired.push(id)
          return
        }
        // Ease from 1 → 0 with cosine curve (smooth out)
        const t = remaining / BIRTH_WINDOW_MS
        const ease = Math.cos((1 - t) * Math.PI * 0.5) // 1 → 0 smooth
        const mat = mesh.material as THREE.MeshBasicMaterial
        mat.opacity = 0.55 * ease
        const scale = 1 + ease * 1.5
        mesh.scale.setScalar(scale)
      })

      for (const id of expired) active.delete(id)

      // Also rotate the brain backdrop slowly
      for (const obj of rotatingObjectsRef.current) {
        obj.rotation.y += 0.0003
        obj.rotation.x += 0.00015
      }

      birthAnimationRafRef.current = requestAnimationFrame(tick)
    }
    birthAnimationRafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(birthAnimationRafRef.current)
  }, [])

  // ─── Custom neuron rendering ─────────────────────────────────────────────
  const nodeThreeObject = useCallback((nodeAny: object) => {
    const node = nodeAny as GraphNode

    const group = new THREE.Group()
    const radius = node.val
    const baseColor = new THREE.Color(node.color)

    // Outer halo — soft daemon-colored glow (slightly HDR)
    const haloGeom = new THREE.SphereGeometry(radius * 1.8, 16, 16)
    const haloColor = baseColor.clone().multiplyScalar(1.2)
    const haloMat = new THREE.MeshBasicMaterial({
      color: haloColor,
      transparent: true,
      opacity: 0.25,
      depthWrite: false,
      toneMapped: false,
    })
    group.add(new THREE.Mesh(haloGeom, haloMat))

    // Core sphere — HDR emissive, what bloom picks up
    const coreGeom = new THREE.SphereGeometry(radius, 20, 20)
    const coreColor = baseColor.clone().multiplyScalar(3.5)
    const coreMat = new THREE.MeshBasicMaterial({
      color: coreColor,
      toneMapped: false,
    })
    group.add(new THREE.Mesh(coreGeom, coreMat))

    // Inner white pulse — pure HDR emission
    const innerGeom = new THREE.SphereGeometry(radius * 0.45, 14, 14)
    const innerColor = new THREE.Color(0xffffff).multiplyScalar(5)
    const innerMat = new THREE.MeshBasicMaterial({
      color: innerColor,
      toneMapped: false,
    })
    group.add(new THREE.Mesh(innerGeom, innerMat))

    // Birth burst — only for newly-seen nodes; animated by the rAF loop above
    if (node.isNew) {
      const burstRadius = radius * 2.5
      const burstGeom = new THREE.SphereGeometry(burstRadius, 16, 16)
      const burstColor = new THREE.Color(0xffd700).multiplyScalar(8)
      const burstMat = new THREE.MeshBasicMaterial({
        color: burstColor,
        transparent: true,
        opacity: 0.55,
        toneMapped: false,
        depthWrite: false,
      })
      const burstMesh = new THREE.Mesh(burstGeom, burstMat)
      group.add(burstMesh)
      node.__burstMesh = burstMesh
      node.__burstEnd = Date.now() + BIRTH_WINDOW_MS
      activeBirthsRef.current.set(node.id, node)
    }

    return group
  }, [])

  const handleNodeClick = useCallback(
    (node: object) => {
      const n = node as GraphNode
      onSelect?.(n.id)
      setSelectedMemoryId(n.id)
    },
    [onSelect],
  )

  const linkColor = useCallback(() => '#88ddff', [])
  const particleColor = useCallback(() => '#ccf0ff', [])

  const graphData = useMemo(
    () => ({ nodes: data.nodes, links: data.links }),
    [data.nodes, data.links],
  )

  return (
    <div ref={containerRef} className="relative w-full h-full bg-[#04080c]">
      <ForceGraph3D
        ref={fgRef}
        width={size.w}
        height={size.h}
        graphData={graphData}
        backgroundColor="rgba(4, 8, 12, 1)"
        showNavInfo={false}
        nodeRelSize={4}
        nodeLabel="name"
        nodeAutoColorBy="daemon"
        nodeThreeObject={nodeThreeObject}
        nodeOpacity={1}
        linkColor={linkColor}
        linkOpacity={0.4}
        linkWidth={0.8}
        linkDirectionalParticles={4}
        linkDirectionalParticleSpeed={0.006}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleColor={particleColor}
        cooldownTicks={Infinity}
        warmupTicks={30}
        d3AlphaDecay={0.01}
        d3VelocityDecay={0.3}
        onNodeClick={handleNodeClick}
      />

      {/* Cinematic HUD overlay — top left */}
      <div className="absolute top-3 left-3 px-4 py-2.5 rounded border border-cyan-400/40 bg-black/70 backdrop-blur-md text-cyan-100 text-[11px] font-mono space-y-0.5 pointer-events-none shadow-[0_0_20px_rgba(68,255,238,0.2)]">
        <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase mb-1">
          The Pool of Memory
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">NEURONS</span>
          <span className="text-cyan-100 font-bold">{stats.nodes}</span>
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">SYNAPSES</span>
          <span className="text-cyan-100 font-bold">{stats.edges}</span>
        </div>
        <div className="flex justify-between gap-6">
          <span className="text-cyan-300/60">LIVE SYNC</span>
          <span className="text-emerald-300 animate-pulse">●</span>
        </div>
        {stats.newInLast > 0 && (
          <div className="flex justify-between gap-6 pt-1 border-t border-cyan-400/20 mt-1">
            <span className="text-amber-300/60">+NEW</span>
            <span className="text-amber-300 font-bold">{stats.newInLast}</span>
          </div>
        )}
        {debugFakeData && (
          <div className="pt-1 border-t border-cyan-400/20 mt-1 text-amber-200/70 text-[9px] tracking-widest">
            DEBUG MODE
          </div>
        )}
      </div>

      {error && (
        <div className="absolute bottom-3 left-3 px-3 py-2 rounded border border-amber-400/40 bg-black/70 text-amber-200 text-[10px] font-mono pointer-events-none">
          ⚠ {error} — the pool is dry
        </div>
      )}

      {!error && data.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-cyan-300/70 text-xs tracking-[0.4em] font-mono animate-pulse">
            THE POOL AWAITS THE FIRST MEMORY
          </div>
        </div>
      )}

      <div className="absolute bottom-3 right-3 text-[9px] text-cyan-300/30 font-mono tracking-widest pointer-events-none">
        DRAG · ORBIT · CLICK A NEURON
      </div>

      <MemoryDetailDrawer
        memoryId={selectedMemoryId}
        onClose={() => setSelectedMemoryId(null)}
      />
    </div>
  )
}
