'use client'

import { useState, useCallback, useEffect } from 'react'
import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { MessageDock } from '@/components/ui/message-dock'
import { Globe } from '@/components/ui/cobe-globe'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'

/**
 * Agents (/agents) — Daemon control
 * VerticalTabs pattern (adapted) for agent sidebar,
 * MessageDock for floating chat, CobeGlobe for ambient lead geography.
 *
 * VerticalTabs from vertical-tabs.tsx uses a hardcoded SERVICES array
 * with external Unsplash images — not usable directly with agent data.
 * The tab animation pattern is reimplemented here for the 4 agents.
 */

const AGENTS = [
  {
    id: 'perseus',
    name: 'Perseus',
    role: 'CEO / Scheduler',
    color: '#58e0ff',
    gradientColors: '#58e0ff, #0ea5e9',
    backgroundColor: 'bg-cyan-400/20',
    description: 'Top-level orchestrator. Schedules all daemon tasks, monitors system health, triggers pipeline runs, and self-audits via MAGMA.',
    tasks: ['Schedule pipeline runs', 'Health monitoring', 'MAGMA self-audit', 'Agent coordination'],
  },
  {
    id: 'titan',
    name: 'Titan',
    role: 'Revenue Engine',
    color: '#39f3e2',
    gradientColors: '#39f3e2, #10b981',
    backgroundColor: 'bg-teal-400/20',
    description: '10-stage revenue pipeline. Ingests leads, enriches via Firecrawl, generates personalized outreach, and closes deals.',
    tasks: ['Lead ingestion', 'Data enrichment', 'Email generation', 'Response handling'],
  },
  {
    id: 'clawdbot',
    name: 'ClawdBot',
    role: 'Site Builder',
    color: '#62f1b5',
    gradientColors: '#62f1b5, #22c55e',
    backgroundColor: 'bg-green-400/20',
    description: 'Builds and deploys personalized demo sites for warm leads. Uses browser automation to capture screenshots and validate deployments.',
    tasks: ['Template selection', 'Content personalization', 'Netlify deployment', 'Screenshot capture'],
  },
  {
    id: 'hermes',
    name: 'Hermes',
    role: 'Alerts / Comms',
    color: '#ffb347',
    gradientColors: '#ffb347, #f97316',
    backgroundColor: 'bg-orange-400/20',
    description: 'Real-time event listener and alert dispatcher. Sends Telegram notifications, powers this War Room dashboard via WebSocket.',
    tasks: ['Event monitoring', 'Telegram alerts', 'WebSocket sync', 'A2A relay'],
  },
]

// Lead geography markers for the globe
const GLOBE_MARKERS = [
  { id: 'us-east', location: [40.71, -74.01] as [number, number], label: 'New York' },
  { id: 'us-west', location: [37.77, -122.42] as [number, number], label: 'San Francisco' },
  { id: 'uk', location: [51.51, -0.13] as [number, number], label: 'London' },
  { id: 'de', location: [52.52, 13.40] as [number, number], label: 'Berlin' },
  { id: 'au', location: [-33.87, 151.21] as [number, number], label: 'Sydney' },
  { id: 'ca', location: [43.65, -79.38] as [number, number], label: 'Toronto' },
]

export default function AgentsPage() {
  const { token } = useToken()
  const { health, events } = useWarRoom()
  const [activeIndex, setActiveIndex] = useState(0)
  const [direction, setDirection] = useState(0)

  const handleTabClick = (index: number) => {
    if (index === activeIndex) return
    setDirection(index > activeIndex ? 1 : -1)
    setActiveIndex(index)
  }

  const handleMessageSend = useCallback(
    (message: string, character: { name: string }, idx: number) => {
      const agent = AGENTS[idx] ?? AGENTS[0]
      console.info(`[MessageDock] → ${agent.name}: ${message}`)
    },
    []
  )

  const activeAgent = AGENTS[activeIndex]

  // Map agents to MessageDock characters
  const dockCharacters = AGENTS.map(agent => ({
    id: agent.id,
    emoji: agent.id === 'perseus' ? '🏛️' : agent.id === 'titan' ? '⚡' : agent.id === 'clawdbot' ? '🤖' : '🪁',
    name: agent.name,
    online: true,
    backgroundColor: agent.backgroundColor,
    gradientColors: agent.gradientColors,
  }))

  const variants = {
    enter: (dir: number) => ({ y: dir > 0 ? '-100%' : '100%', opacity: 0 }),
    center: { zIndex: 1, y: 0, opacity: 1 },
    exit: (dir: number) => ({ zIndex: 0, y: dir > 0 ? '100%' : '-100%', opacity: 0 }),
  }

  // Recent events filtered for this agent
  const agentEvents = (events ?? [])
    .filter(e => {
      if (!activeAgent) return false
      const str = JSON.stringify(e).toLowerCase()
      return str.includes(activeAgent.id) || str.includes(activeAgent.name.toLowerCase())
    })
    .slice(0, 5)

  return (
    <div className="space-y-4 pb-24">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-foreground">Agents</h1>
        <span className="text-xs text-muted-foreground">Daemon control — communicate, monitor, review</span>
      </div>

      <div className="grid lg:grid-cols-12 gap-6 items-start">
        {/* Left: Vertical agent tabs (VerticalTabs pattern adapted for agents) */}
        <div className="lg:col-span-4 flex flex-col">
          <div className="space-y-1 mb-6">
            <h2 className="text-lg font-medium text-foreground tracking-tight">Active Daemons</h2>
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-[0.3em]">
              (4 RUNNING)
            </span>
          </div>

          <div className="flex flex-col space-y-0">
            {AGENTS.map((agent, index) => {
              const isActive = activeIndex === index
              return (
                <button
                  key={agent.id}
                  onClick={() => handleTabClick(index)}
                  className={cn(
                    'group relative flex items-start gap-4 py-5 text-left transition-all duration-300 border-t border-border/30 first:border-0',
                    isActive ? 'text-foreground' : 'text-muted-foreground/60 hover:text-foreground'
                  )}
                >
                  {/* Progress indicator bar */}
                  <div className="absolute left-[-16px] top-0 bottom-0 w-[2px] bg-border/30">
                    {isActive && (
                      <motion.div
                        className="absolute top-0 left-0 w-full origin-top"
                        style={{ backgroundColor: agent.color }}
                        initial={{ height: '0%' }}
                        animate={{ height: '100%' }}
                        transition={{ duration: 0.4, ease: 'easeOut' }}
                      />
                    )}
                  </div>

                  <span className="text-[9px] font-medium mt-1 tabular-nums opacity-50">
                    /{String(index + 1).padStart(2, '0')}
                  </span>

                  <div className="flex flex-col gap-1.5 flex-1">
                    <div className="flex items-center gap-2">
                      <img
                        src={`/assets/generated/agents/${agent.id}.svg`}
                        alt={agent.name}
                        className="w-6 h-6 rounded-full shrink-0"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                      <span className={cn(
                        'text-xl font-normal tracking-tight transition-colors duration-300',
                        isActive ? 'text-foreground' : ''
                      )}>
                        {agent.name}
                      </span>
                      {/* Online dot */}
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                    </div>

                    <AnimatePresence mode="wait">
                      {isActive && (
                        <motion.div
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 'auto' }}
                          exit={{ opacity: 0, height: 0 }}
                          transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
                          className="overflow-hidden"
                        >
                          <p className="text-muted-foreground text-xs leading-relaxed max-w-xs pb-1">
                            {agent.role} — {agent.description.slice(0, 80)}…
                          </p>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Right: Agent detail panel + Globe */}
        <div className="lg:col-span-8 flex flex-col gap-4">
          {/* Agent detail card with animated slide */}
          <div className="relative overflow-hidden rounded-2xl border border-border/50 bg-card min-h-[320px]">
            <AnimatePresence initial={false} custom={direction} mode="popLayout">
              <motion.div
                key={activeIndex}
                custom={direction}
                variants={variants}
                initial="enter"
                animate="center"
                exit="exit"
                transition={{
                  y: { type: 'spring', stiffness: 260, damping: 32 },
                  opacity: { duration: 0.3 },
                }}
                className="absolute inset-0 p-6"
              >
                {/* Agent header */}
                <div className="flex items-center gap-4 mb-5">
                  <div
                    className="w-14 h-14 rounded-xl flex items-center justify-center"
                    style={{ background: `linear-gradient(135deg, ${activeAgent.gradientColors})` }}
                  >
                    <img
                      src={`/assets/generated/agents/${activeAgent.id}.svg`}
                      alt={activeAgent.name}
                      className="w-10 h-10 rounded-lg"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement
                        target.style.display = 'none'
                      }}
                    />
                  </div>
                  <div>
                    <h3 className="text-xl font-bold text-foreground">{activeAgent.name}</h3>
                    <p className="text-xs text-muted-foreground">{activeAgent.role}</p>
                  </div>
                  <div className="ml-auto flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                    <span className="text-xs text-green-400 font-mono">running</span>
                  </div>
                </div>

                <p className="text-sm text-muted-foreground mb-5 leading-relaxed">
                  {activeAgent.description}
                </p>

                {/* Task list */}
                <div className="grid grid-cols-2 gap-2 mb-5">
                  {activeAgent.tasks.map((task, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 text-xs text-muted-foreground bg-white/5 rounded-lg px-3 py-2"
                    >
                      <span className="w-1 h-1 rounded-full shrink-0" style={{ backgroundColor: activeAgent.color }} />
                      {task}
                    </div>
                  ))}
                </div>

                {/* Recent events for this agent */}
                {agentEvents.length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground/60 uppercase tracking-widest mb-2">Recent Activity</p>
                    <div className="space-y-1">
                      {agentEvents.map((event: any, i) => (
                        <div key={i} className="text-xs text-muted-foreground bg-white/5 rounded px-3 py-1.5 truncate">
                          {event.message ?? event.type ?? JSON.stringify(event).slice(0, 80)}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>

          {/* Lead geography globe */}
          <div className="glass-card hud-panel rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <p className="text-sm font-medium text-foreground">Lead Geography</p>
                <p className="text-[10px] text-muted-foreground">Active prospect locations</p>
              </div>
              <span className="text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest">
                {GLOBE_MARKERS.length} regions
              </span>
            </div>
            <div className="flex justify-center">
              <Globe
                className="w-64 h-64"
                markers={GLOBE_MARKERS}
                dark={1}
                baseColor={[0.1, 0.1, 0.15]}
                markerColor={[0.35, 0.95, 0.8]}
                glowColor={[0.2, 0.8, 0.7]}
                mapBrightness={4}
                speed={0.004}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Floating MessageDock — agent chat */}
      <MessageDock
        characters={dockCharacters}
        theme="dark"
        onMessageSend={handleMessageSend}
        placeholder={(name) => `Message ${name}...`}
        closeOnSend={false}
      />
    </div>
  )
}
