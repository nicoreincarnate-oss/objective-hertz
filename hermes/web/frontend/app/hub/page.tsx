'use client'

import { useState, useEffect, useCallback, useRef, type ComponentType } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import ConcentricLoader from '@/components/ui/concentric-loader'
import { PixelPanel, type PanelVariant } from '@/components/olympus/PixelPanel'
import {
  CaduceusHealthPanel,
  WarTableEventsPanel,
  UrgentApprovalsPanel,
  HermesChatPanel,
  ScribeBriefingPanel,
  DaemonsPanel,
  OwlMetricsPanel,
  TortoiseLorePanel,
} from '@/components/olympus/HermesPanels'
import {
  CelestialClockPanel,
  HealthOrbsPanel,
  RecentRunsPanel,
  ManualTriggerPanel,
  EagleReportsPanel,
  BackupLorePanel,
} from '@/components/olympus/PerseusPanels'
import {
  PipelineStallsPanel,
  FountainTickerPanel,
  RevenueScalePanel,
  ActiveCampaignsPanel,
  BlockedLeadsPanel,
  BackgroundLorePanel,
} from '@/components/olympus/TitanPanels'
import {
  BrowserMirrorPanel,
  LoomBuildPanel,
  BuiltSitesPanel,
  DeploymentDockPanel,
  SkillLibraryPanel,
  SpiderCrawlPanel,
} from '@/components/olympus/ClawdbotPanels'
import {
  VaultWalletsPanel,
  LedgerPanel,
  SurvivalTierPanel,
  ScalesOfCommercePanel,
  BurnRatePanel,
  MintPanel,
} from '@/components/olympus/ConwayPanels'
import {
  TriageWallPanel,
  SwarmPitPanel,
  RuffDogPanel,
  PytestArenaPanel,
  PatternJarsPanel,
  HeisenbugLorePanel,
} from '@/components/olympus/RufloPanels'
import {
  MemorySearchPanel,
  OracleChatPanel,
  MemoryDomainsPanel,
  MemoryStatsPanel,
  MemoryVersionsPanel,
  RecentMemoriesPanel,
} from '@/components/olympus/OracleMemoryPanels'
import {
  ZeusThronePanel,
  WorkflowsPanel,
  FeatureFlagsPanel,
  CrowdActivityPanel,
  GoalsTreePanel,
  DecisionsLogPanel,
} from '@/components/olympus/JarvisPanels'
import { Graph3DPanel } from '@/components/olympus/Graph3DPanel'

interface RoomHotspot {
  id: string
  label: string
  sub: string
  x: number
  y: number
  w: number
  h: number
  color: string
  action: string
  endpoint?: string | null
}

// Maps every (roomId, hotspotId) -> panel content component.
// RoomView picks the right panel by looking up ROOM_PANELS[roomId][hotspotId].
const ROOM_PANELS: Record<string, Record<string, ComponentType>> = {
  hermes: {
    caduceus: CaduceusHealthPanel,
    'war-table': WarTableEventsPanel,
    'urgent-slot': UrgentApprovalsPanel,
    'hermes-fresco': HermesChatPanel,
    'scribe-ledger': ScribeBriefingPanel,
    'pigeon-wall-left': DaemonsPanel,
    'pigeon-wall-right': DaemonsPanel,
    'owl-bookshelf': OwlMetricsPanel,
    tortoise: TortoiseLorePanel,
  },
  perseus: {
    'celestial-clock': CelestialClockPanel,
    'health-orbs': HealthOrbsPanel,
    'hourglass-shelf': RecentRunsPanel,
    telescope: ManualTriggerPanel,
    'bronze-eagle': EagleReportsPanel,
    'sleeping-apprentice': BackupLorePanel,
  },
  titan: {
    'instantly-barrels': ActiveCampaignsPanel,
    'email-campaigns-altar': PipelineStallsPanel,
    'produce-stand': BlockedLeadsPanel,
    'api-bottles': FountainTickerPanel,
    'v2-amphora': RevenueScalePanel,
    'openjarvis-graffiti': BackgroundLorePanel,
  },
  clawdbot: {
    'browser-mirror': BrowserMirrorPanel,
    loom: LoomBuildPanel,
    'build-table': BuiltSitesPanel,
    'deployment-dock': DeploymentDockPanel,
    'skill-bookshelf': SkillLibraryPanel,
    'spider-web': SpiderCrawlPanel,
  },
  conway: {
    'vault-wall': VaultWalletsPanel,
    'great-ledger': LedgerPanel,
    'survival-tier': SurvivalTierPanel,
    'balance-scale': ScalesOfCommercePanel,
    'burn-chalkboard': BurnRatePanel,
    mint: MintPanel,
  },
  ruflo: {
    'triage-wall': TriageWallPanel,
    'swarm-pit': SwarmPitPanel,
    'ruff-dog': RuffDogPanel,
    'pytest-arena': PytestArenaPanel,
    'pattern-jars': PatternJarsPanel,
    heisenbug: HeisenbugLorePanel,
  },
  memory: {
    'pool-center': MemorySearchPanel,
    pythia: OracleChatPanel,
    'floating-islands': MemoryDomainsPanel,
    'atlas-globe': MemoryStatsPanel,
    ouroboros: MemoryVersionsPanel,
    'pandoras-box': RecentMemoriesPanel,
  },
  openjarvis: {
    'zeus-throne': ZeusThronePanel,
    'holographic-scrolls': WorkflowsPanel,
    'stained-glass': FeatureFlagsPanel,
    crowd: CrowdActivityPanel,
    'constellation-ceiling': GoalsTreePanel,
    'bronze-doors': DecisionsLogPanel,
  },
}

const LOADING_SCREENS = [
  '/olympus/loading/birth.png',
  '/olympus/loading/labyrinth.png',
  '/olympus/loading/constellation.png',
  '/olympus/loading/council.png',
  '/olympus/loading/odyssey.png',
  '/olympus/loading/tapestry.png',
]

interface HotspotData {
  id: string; label: string; sub: string
  x: number; y: number; w: number; h: number
  color: string; action: string
}

// Loaded at runtime from /olympus/hotspots.json (edit at /hub/editor)
const FALLBACK_BUILDINGS = [
  { id: 'perseus', name: 'THE OBSERVATORY', sub: 'Perseus · Scheduler', x: 19, y: 37, w: 4, h: 6, color: '#6688ff' },
]

function CursorSprite({ x, y }: { x: number; y: number }) {
  return (
    <div
      className="pointer-events-none fixed"
      style={{
        left: x - 24,
        top: y - 48,
        zIndex: 2147483647, // max z-index, above everything including modals
      }}
    >
      <img
        src="/olympus/cursor-perseus.png"
        alt=""
        width={48}
        height={48}
        className="drop-shadow-[0_0_8px_rgba(255,200,80,0.4)]"
        style={{ imageRendering: 'pixelated' }}
        draggable={false}
      />
    </div>
  )
}

type Building = { id: string; name: string; sub: string; x: number; y: number; w: number; h: number; color: string }

function LoadingScreen({ building }: { building: Building }) {
  // Pick a random loading image on client only to avoid hydration mismatch
  const [bgImage, setBgImage] = useState(LOADING_SCREENS[0])
  useEffect(() => {
    setBgImage(LOADING_SCREENS[Math.floor(Math.random() * LOADING_SCREENS.length)])
  }, [])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[60] flex flex-col items-center justify-center"
      style={{ cursor: 'none' }}
    >
      {/* Background loading screen — FULLSCREEN like a game loading screen */}
      <img
        src={bgImage}
        alt=""
        className="absolute inset-0 w-full h-full object-cover"
      />
      {/* Subtle darkening so the ripple loader is visible */}
      <div className="absolute inset-0 bg-black/30" />
      <div
        className="absolute inset-0"
        style={{ background: 'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.5) 100%)' }}
      />

      {/* Content — centered */}
      <div className="relative z-10 flex flex-col items-center">
        {/* Ripple pulse loader — concentric circles */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
        >
          <ConcentricLoader />
        </motion.div>

        {/* Building name below the ripple */}
        <motion.div
          initial={{ y: 10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mt-8 text-center"
        >
          <h2 className="text-2xl font-bold text-amber-100 tracking-[0.3em] drop-shadow-[0_2px_12px_rgba(0,0,0,0.9)]">
            {building.name}
          </h2>
          <p className="text-xs text-amber-400/60 tracking-[0.4em] mt-1 drop-shadow-[0_2px_8px_rgba(0,0,0,0.9)]">
            {building.sub}
          </p>
        </motion.div>
      </div>

      {/* Bottom hint */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
        className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 text-[9px] text-white/20 tracking-[0.5em]"
      >
        ENTERING THE AGORA
      </motion.div>
    </motion.div>
  )
}

function RoomView({ id, onClose, buildings }: { id: string; onClose: () => void; buildings: Building[] }) {
  const building = buildings.find(b => b.id === id)!
  const [imgLoaded, setImgLoaded] = useState(false)
  const [hotspots, setHotspots] = useState<RoomHotspot[]>([])
  const [hovered, setHovered] = useState<string | null>(null)
  const [openPanel, setOpenPanel] = useState<string | null>(null)
  const [imgBox, setImgBox] = useState({ left: 0, top: 0, width: 0, height: 0 })
  const imgRef = useRef<HTMLImageElement>(null)

  // Track the rendered image bounds (object-contain letterboxes)
  const updateBox = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    const rect = img.getBoundingClientRect()
    setImgBox({ left: rect.left, top: rect.top, width: rect.width, height: rect.height })
  }, [])

  useEffect(() => {
    updateBox()
    window.addEventListener('resize', updateBox)
    return () => window.removeEventListener('resize', updateBox)
  }, [updateBox])

  // Load hotspots for this room from the merged hotspots.json
  useEffect(() => {
    fetch('/olympus/hotspots.json')
      .then((r) => r.json())
      .then((data) => {
        const sceneKey = `room-${id}`
        const scene = data?.scenes?.[sceneKey]
        if (scene?.hotspots) setHotspots(scene.hotspots)
      })
      .catch(() => setHotspots([]))
  }, [id])

  // ESC closes panel first, then room
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (openPanel) setOpenPanel(null)
        else onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openPanel, onClose])

  const activeHotspot = hotspots.find((h) => h.id === openPanel) ?? null
  const roomPanels = ROOM_PANELS[id] ?? {}
  const PanelContent = activeHotspot ? roomPanels[activeHotspot.id] : null
  const panelVariant: PanelVariant = activeHotspot
    ? (activeHotspot.action.replace('panel:', '') as PanelVariant)
    : 'stone'

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.4 }}
      className="fixed inset-0 z-[70] bg-black"
      style={{ cursor: 'none' }}
    >
      {/* Room image — fullscreen */}
      <div className="absolute inset-4 flex items-center justify-center">
        <motion.img
          ref={imgRef}
          src={`/olympus/rooms/${id}.png`}
          alt={building.name}
          initial={{ scale: 1.05, opacity: 0 }}
          animate={{ scale: 1, opacity: imgLoaded ? 1 : 0 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
          className="max-w-full max-h-full object-contain"
          onLoad={() => {
            setImgLoaded(true)
            updateBox()
          }}
        />
      </div>

      {/* Vignette overlay */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.6) 100%)',
        }}
      />

      {/* Hotspot overlay layer — positioned over the image bounds */}
      {imgLoaded && imgBox.width > 0 && (
        <div
          className="absolute pointer-events-none"
          style={{
            left: imgBox.left,
            top: imgBox.top,
            width: imgBox.width,
            height: imgBox.height,
          }}
        >
          {hotspots.map((h) => (
            <button
              key={h.id}
              onClick={() => setOpenPanel(h.id)}
              onMouseEnter={() => setHovered(h.id)}
              onMouseLeave={() => setHovered(null)}
              className="absolute pointer-events-auto transition-all duration-200 group"
              style={{
                left: `${h.x}%`,
                top: `${h.y}%`,
                width: `${h.w}%`,
                height: `${h.h}%`,
                cursor: 'none',
                border: hovered === h.id ? `2px solid ${h.color}` : '2px solid transparent',
                borderRadius: '6px',
                background: hovered === h.id ? `${h.color}1a` : 'transparent',
                boxShadow: hovered === h.id ? `0 0 30px ${h.color}66, inset 0 0 20px ${h.color}33` : 'none',
              }}
            >
              {hovered === h.id && (
                <div
                  className="absolute -top-7 left-0 px-2 py-1 rounded font-pixel-header text-[8px] whitespace-nowrap"
                  style={{
                    background: 'rgba(0,0,0,0.85)',
                    border: `1px solid ${h.color}66`,
                    color: h.color,
                  }}
                >
                  {h.label}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Top bar — room name */}
      <motion.div
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.5, duration: 0.4 }}
        className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-6 py-4 pointer-events-none"
        style={{ background: 'linear-gradient(180deg, rgba(0,0,0,0.7) 0%, transparent 100%)' }}
      >
        <div className="flex items-center gap-3">
          <div
            className="w-2.5 h-2.5 rounded-full"
            style={{ background: building.color, boxShadow: `0 0 12px ${building.color}` }}
          />
          <div>
            <h2 className="text-lg font-bold text-amber-100 tracking-[0.2em] font-pixel-header">{building.name}</h2>
            <p className="text-[10px] text-amber-500/50 tracking-[0.3em]">{building.sub}</p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="pointer-events-auto px-4 py-2 rounded-lg text-amber-300/60 hover:text-amber-100 hover:bg-white/5 transition-all text-sm tracking-wider font-pixel-header"
          style={{ cursor: 'none', fontSize: '10px' }}
        >
          ESC — RETURN
        </button>
      </motion.div>

      {/* Bottom hint */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1, duration: 0.5 }}
        className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 text-[10px] text-white/20 tracking-widest font-pixel-header"
      >
        click on objects · ESC — return
      </motion.div>

      {/* Panel modal — Oracle's Pool center gets the 3D brain panel directly */}
      <AnimatePresence>
        {activeHotspot && id === 'memory' && activeHotspot.id === 'pool-center' ? (
          <Graph3DPanel onClose={() => setOpenPanel(null)} />
        ) : activeHotspot && PanelContent ? (
          <PixelPanel
            variant={panelVariant}
            title={activeHotspot.label}
            subtitle={activeHotspot.sub}
            onClose={() => setOpenPanel(null)}
          >
            <PanelContent />
          </PixelPanel>
        ) : null}
      </AnimatePresence>
    </motion.div>
  )
}

export default function HubPage() {
  const [cursor, setCursor] = useState({ x: 0, y: 0 })
  const [hovered, setHovered] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [buildings, setBuildings] = useState<{ id: string; name: string; sub: string; x: number; y: number; w: number; h: number; color: string }[]>([])
  const [imgBounds, setImgBounds] = useState({ left: 0, top: 0, width: 0, height: 0 })
  const imgRef = useRef<HTMLImageElement>(null)

  // Track the rendered image bounds so hotspots align perfectly
  const updateBounds = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    const rect = img.getBoundingClientRect()
    if (rect.width === 0) return // not laid out yet
    setImgBounds({ left: rect.left, top: rect.top, width: rect.width, height: rect.height })
  }, [])

  useEffect(() => {
    updateBounds()
    // Resize listener
    window.addEventListener('resize', updateBounds)
    // ResizeObserver — fires whenever the image's rendered size changes for any reason
    let observer: ResizeObserver | null = null
    if (imgRef.current && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => updateBounds())
      observer.observe(imgRef.current)
    }
    // Animation frame fallback — ensures bounds are read after first paint
    const raf = requestAnimationFrame(() => updateBounds())
    // Backup timeout — handles cached images that don't fire onLoad
    const t = setTimeout(updateBounds, 200)
    return () => {
      window.removeEventListener('resize', updateBounds)
      observer?.disconnect()
      cancelAnimationFrame(raf)
      clearTimeout(t)
    }
  }, [updateBounds])

  // Load hotspots from JSON
  useEffect(() => {
    fetch('/olympus/hotspots.json')
      .then(r => r.json())
      .then(data => {
        const hs = data.scenes?.town?.hotspots || []
        setBuildings(hs.map((h: HotspotData) => ({ id: h.id, name: h.label, sub: h.sub, x: h.x, y: h.y, w: h.w, h: h.h, color: h.color })))
      })
      .catch(() => setBuildings(FALLBACK_BUILDINGS))
  }, [])

  const handleClick = useCallback((id: string) => {
    setLoading(id)
    setTimeout(() => { setLoading(null); setActive(id) }, 2500)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setActive(null); setLoading(null) } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="relative w-screen h-screen overflow-hidden select-none" style={{ cursor: 'none', background: '#0a0618' }}
      onMouseMove={(e) => setCursor({ x: e.clientX, y: e.clientY })}>
      {/* Town image */}
      <div className="absolute inset-0 flex items-center justify-center">
        <img
          ref={imgRef}
          src="/olympus/town.png"
          alt="Mount Olympus"
          className="max-w-full max-h-full object-contain"
          draggable={false}
          onLoad={updateBounds}
        />
      </div>
      {/* Hotspots — fixed positioning over the rendered image bounds */}
      {imgBounds.width > 0 && (
        <div
          className="fixed pointer-events-none"
          style={{
            left: imgBounds.left,
            top: imgBounds.top,
            width: imgBounds.width,
            height: imgBounds.height,
          }}
        >
          {buildings.map((b) => (
            <button
              key={b.id}
              className="absolute pointer-events-auto transition-all duration-200"
              style={{
                left: `${b.x}%`,
                top: `${b.y}%`,
                width: `${b.w}%`,
                height: `${b.h}%`,
                cursor: 'none',
                border: `2px solid ${b.color}${hovered === b.id ? 'ff' : '66'}`,
                borderRadius: '8px',
                background: hovered === b.id ? `${b.color}26` : `${b.color}10`,
                boxShadow: hovered === b.id
                  ? `0 0 24px ${b.color}, inset 0 0 16px ${b.color}55`
                  : `0 0 8px ${b.color}55`,
                animation: 'olympus-hotspot-pulse 2.5s ease-in-out infinite',
              }}
              onMouseEnter={() => setHovered(b.id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => handleClick(b.id)}
            />
          ))}
        </div>
      )}
      {/* Hover tooltip */}
      <AnimatePresence>
        {hovered && !active && !loading && (
          <motion.div initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="fixed z-40 pointer-events-none px-4 py-2 rounded-lg"
            style={{ left: cursor.x + 20, top: cursor.y - 10, background: 'rgba(10,6,24,0.9)', border: `1px solid ${buildings.find(b => b.id === hovered)?.color}44` }}>
            <p className="text-sm font-bold text-amber-100 tracking-wider">{buildings.find(b => b.id === hovered)?.name}</p>
            <p className="text-[10px] text-amber-500/50 tracking-widest">{buildings.find(b => b.id === hovered)?.sub}</p>
          </motion.div>
        )}
      </AnimatePresence>
      {/* Cursor sprite — always rendered, sits above everything */}
      <CursorSprite x={cursor.x} y={cursor.y} />
      {/* HUD */}
      <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-30 text-[10px] text-white/20 tracking-widest">CLICK — enter · ESC — back</div>
      <div className="fixed top-4 left-1/2 -translate-x-1/2 z-30 text-center">
        <h1 className="text-lg font-bold text-amber-100/60 tracking-[0.3em]">MOUNT OLYMPUS</h1>
        <p className="text-[9px] text-amber-500/30 tracking-[0.5em]">PERSEUS COMMAND CENTER</p>
      </div>
      {/* Loading + Dashboard */}
      <AnimatePresence>{loading && <LoadingScreen building={buildings.find(b => b.id === loading)!} />}</AnimatePresence>
      <AnimatePresence>{active && <RoomView id={active} onClose={() => setActive(null)} buildings={buildings} />}</AnimatePresence>
    </div>
  )
}
