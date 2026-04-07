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

// ─── Phase 43 — Anatomical brain modules ────────────────────────────────────
import { loadBrainMesh, type BrainMeshHandle } from './brain/BrainMesh'
import {
  createInstancedNeurons,
  type InstancedNeuronsHandle,
  type NeuronInput,
} from './brain/InstancedNeurons'
import {
  createInstancedSynapses,
  type InstancedSynapsesHandle,
  type SynapseInput,
} from './brain/InstancedSynapses'
import {
  createAmbientParticles,
  type AmbientParticlesHandle,
} from './brain/AmbientParticles'
import {
  createAmbientNeurons,
  type AmbientNeuronsHandle,
} from './brain/AmbientNeurons'
import {
  createActionPotentialEngine,
  type ActionPotentialEngine,
} from './brain/ActionPotential'
import { createCinematics, type CinematicsHandle } from './brain/Cinematics'
import { regionForDaemon, allRegions, sampleInRegion } from './brain/BrainRegions'

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
  const rotatingObjectsRef = useRef<THREE.Object3D[]>([])
  const birthAnimationRafRef = useRef<number>(0)

  // ─── Phase 43 brain module handles ─────────────────────────────────────────
  const brainMeshRef = useRef<BrainMeshHandle | null>(null)
  const neuronsRef = useRef<InstancedNeuronsHandle | null>(null)
  const synapsesRef = useRef<InstancedSynapsesHandle | null>(null)
  const ambientNeuronsRef = useRef<AmbientNeuronsHandle | null>(null)
  const ambientParticlesRef = useRef<AmbientParticlesHandle | null>(null)
  const actionPotentialRef = useRef<ActionPotentialEngine | null>(null)
  const cinematicsRef = useRef<CinematicsHandle | null>(null)
  const brainTickRafRef = useRef<number>(0)
  const lastTickTimeRef = useRef<number>(0)

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

    // ─── Phase 43: feed brain modules ────────────────────────────────────
    const neurons = neuronsRef.current
    const synapses = synapsesRef.current
    const actionPotential = actionPotentialRef.current

    if (neurons) {
      // Sample anatomical positions per daemon, deterministic per node ID
      // Build a per-daemon counter so each node gets a unique slot in its region
      const daemonCounts: Record<string, number> = {}
      const positionMap = new Map<string, THREE.Vector3>()

      // Group nodes by daemon
      const nodesByDaemon: Record<string, GraphNode[]> = {}
      for (const n of nextNodes) {
        if (!nodesByDaemon[n.daemon]) nodesByDaemon[n.daemon] = []
        nodesByDaemon[n.daemon].push(n)
      }

      // For each daemon, sample N positions in its anatomical region
      for (const [daemon, nodes] of Object.entries(nodesByDaemon)) {
        const region = regionForDaemon(daemon)
        const samples = sampleInRegion(region, nodes.length, 200)
        for (let i = 0; i < nodes.length; i++) {
          positionMap.set(nodes[i].id, samples[i])
          daemonCounts[daemon] = (daemonCounts[daemon] ?? 0) + 1
        }
      }

      const neuronInputs: NeuronInput[] = nextNodes.map((n) => ({
        id: n.id,
        position: positionMap.get(n.id) ?? new THREE.Vector3(0, 0, 0),
        color: n.color,
        size: 2 + n.confidence * 3,
        intensity: n.isNew ? 1.6 : 1.1,
      }))
      neurons.setNodes(neuronInputs)

      // Build synapse inputs from links — need source and target positions
      if (synapses) {
        const synapseInputs: SynapseInput[] = []
        const adjacencyMap = new Map<string, number[]>()
        nextLinks.forEach((link, edgeIndex) => {
          const fromPos = positionMap.get(link.source)
          const toPos = positionMap.get(link.target)
          if (!fromPos || !toPos) return
          synapseInputs.push({
            from: fromPos,
            to: toPos,
            color: '#88c8ff',
            intensity: 0.8,
          })
          // Track adjacency for action potential cascades
          const list = adjacencyMap.get(link.source) ?? []
          list.push(edgeIndex)
          adjacencyMap.set(link.source, list)
        })
        synapses.setEdges(synapseInputs)
        actionPotential?.registerSynapses(adjacencyMap)
      }

      // Fire action potentials from any newly-arrived nodes
      if (actionPotential) {
        const newNodeIds = nextNodes.filter((n) => n.isNew).map((n) => n.id)
        if (newNodeIds.length > 0) {
          actionPotential.fireBurst(newNodeIds)
        }
      }
    }
  }, [])

  // Auto-fallback flag — set when /api/memory/graph is unreachable or empty.
  // Renders the demo graph instead so the visualization isn't a black void.
  const [fallbackActive, setFallbackActive] = useState(false)

  const loadDemoGraph = useCallback(() => {
    ingestGraphData(makeFakeData())
    setFallbackActive(true)
    setError(null)
  }, [ingestGraphData])

  const fetchGraph = useCallback(async () => {
    if (debugFakeData) {
      loadDemoGraph()
      return
    }
    try {
      const res = await fetch('/api/memory/graph?depth=3&limit=500', { cache: 'no-store' })
      if (!res.ok) {
        // API down (Hermes FastAPI not running, 401 auth, etc.) → demo fallback
        loadDemoGraph()
        return
      }
      const incoming = (await res.json()) as ServerGraphData
      if (!incoming?.nodes || incoming.nodes.length === 0) {
        // API up but no real memories yet → demo fallback so the brain isn't empty
        loadDemoGraph()
        return
      }
      setFallbackActive(false)
      setError(null)
      ingestGraphData(incoming)
    } catch {
      // Network error → demo fallback
      loadDemoGraph()
    }
  }, [debugFakeData, ingestGraphData, loadDemoGraph])

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
      renderer.toneMappingExposure = 1.0
      renderer.outputColorSpace = THREE.SRGBColorSpace

      // ── 2. Unreal Bloom — subtle, only the brightest pixels glow ─────────
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
        0.55, // strength — much lower than 1.6
        0.4, // radius — tighter falloff
        0.5, // threshold — only pixels brighter than 0.5 bloom (not everything)
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
        color: new THREE.Color(0x6688aa),
        size: 1.8,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.45,
        fog: false,
        toneMapped: true,
      })
      const stars = new THREE.Points(starGeom, starMat)
      stars.name = 'starfield'
      stars.frustumCulled = false
      scene.add(stars)

      // ── 5. Anatomical brain mesh (Phase 43 module) ───────────────────────
      // Loads /olympus/brain/cortex.glb if present, else procedural fallback
      loadBrainMesh()
        .then((handle) => {
          brainMeshRef.current = handle
          // Scale the unit-radius brain to ~200 world units to match neuron scale
          handle.mesh.scale.setScalar(200)
          scene.add(handle.mesh)
          rotatingObjectsRef.current.push(handle.mesh)
        })
        .catch(() => {
          /* loadBrainMesh always resolves; never reached */
        })

      // ── 6. Phase 43 instanced neuron + synapse + ambient layers ──────────
      const neurons = createInstancedNeurons(50000)
      neuronsRef.current = neurons
      scene.add(neurons.group)

      const synapses = createInstancedSynapses(50000)
      synapsesRef.current = synapses
      scene.add(synapses.group)

      const ambientNeurons = createAmbientNeurons(8000)
      ambientNeurons.setRegions(allRegions())
      ambientNeuronsRef.current = ambientNeurons
      scene.add(ambientNeurons.group)

      const ambientParticles = createAmbientParticles(15000)
      ambientParticles.setBounds(
        new THREE.Box3(
          new THREE.Vector3(-220, -220, -220),
          new THREE.Vector3(220, 220, 220),
        ),
      )
      ambientParticlesRef.current = ambientParticles
      scene.add(ambientParticles.points)

      const actionPotential = createActionPotentialEngine()
      // Wire pulse callback so action potentials trigger synapse glows
      actionPotential.registerCallback((edgeIndex) => {
        synapsesRef.current?.firePulse(edgeIndex)
      })
      actionPotentialRef.current = actionPotential

      const cinematics = createCinematics(
        // The library's controls.object is the camera
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (controls as any).object as THREE.Camera,
      )
      cinematics.startCorticalWaves()
      cinematicsRef.current = cinematics

      // ── 7. Subtle rim light via a point light (very low intensity) ───────
      const rimLight = new THREE.PointLight(0x88c8ff, 0.4, 2000, 1)
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

  // ─── Phase 43 brain tick loop ────────────────────────────────────────────
  // Drives all brain modules every frame (synapse pulses, ambient neuron
  // phase modulation, particle physics, action potential cascades, cortical
  // wave phase, fly-to camera tweens). Separate from the birth-animation rAF
  // so they can run independently and one can be paused without stopping the
  // other.
  useEffect(() => {
    const tickBrain = () => {
      const now = performance.now()
      const lastTick = lastTickTimeRef.current || now
      const dt = Math.min(1 / 15, (now - lastTick) / 1000) // cap at 67ms
      lastTickTimeRef.current = now

      synapsesRef.current?.tick(dt)
      ambientNeuronsRef.current?.tick(dt)
      ambientParticlesRef.current?.tick(
        dt,
        // The particles handle accepts an optional renderer arg for future GPGPU upgrade
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (fgRef.current as any)?.renderer?.() ?? (undefined as unknown as THREE.WebGLRenderer),
      )
      actionPotentialRef.current?.tick(dt)
      cinematicsRef.current?.tick(dt)

      brainTickRafRef.current = requestAnimationFrame(tickBrain)
    }
    brainTickRafRef.current = requestAnimationFrame(tickBrain)
    return () => cancelAnimationFrame(brainTickRafRef.current)
  }, [])

  // ─── Custom neuron rendering ─────────────────────────────────────────────
  // Phase 43: We render neurons via InstancedNeurons (50k capacity, single
  // draw call). The library's per-node Object3D pipeline is now disabled —
  // we return an empty Group so the library still tracks node positions
  // for the force simulation, but doesn't render anything visible at the
  // node positions. The visible neurons come from neuronsRef.current.
  const nodeThreeObject = useCallback(() => {
    return new THREE.Group() // empty — InstancedNeurons handles rendering
  }, [])

  const handleNodeClick = useCallback(
    (node: object) => {
      const n = node as GraphNode
      onSelect?.(n.id)
      setSelectedMemoryId(n.id)
      // Phase 43: also fire an action potential cascade from this node
      actionPotentialRef.current?.fireFromNode(n.id, 1)
    },
    [onSelect],
  )

  const linkColor = useCallback(() => 'rgba(0,0,0,0)', []) // hide library links — InstancedSynapses renders them
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
        nodeOpacity={0}
        linkColor={linkColor}
        linkOpacity={0}
        linkWidth={0}
        linkDirectionalParticles={0}
        linkDirectionalParticleSpeed={0}
        linkDirectionalParticleWidth={0}
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
