'use client'

import { useState, useCallback } from 'react'
import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { MessageDock } from '@/components/ui/message-dock'
import { Globe } from '@/components/ui/cobe-globe'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'

/**
 * Agents (/agents) — Daemon control
 * VerticalTabs pattern (adapted) for agent sidebar,
 * MessageDock for floating chat, CobeGlobe as its own "Lead Geography" section.
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
    <div className="space-y-8 pb-24">
      {/* Page header */}
      <div>
        <HyperText text="Agents" className="text-2xl font-bold text-foreground" />
        <p className="text-xs text-muted-foreground mt-1">Daemon control — communicate, monitor, review</p>
      </div>

      <div className="grid lg:grid-cols-12 gap-6 items-start">
        {/* Left (4 cols): Vertical agent tabs */}
        <div className="lg:col-span-4 flex flex-col">
          <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
            Active Daemons
          </p>

          <div className="flex flex-col space-y-0">
            {AGENTS.map((agent, index) => {
              const isActive = activeIndex === index
              return (
                <button
                  key={agent.id}
                  onClick={() => handleTabClick(index)}
                  className={cn(
                    'group relative flex items-start gap-4 py-5 text-left cursor-pointer transition-all duration-200 border-t border-border/30 first:border-0',
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
                      <HyperText
                        text={agent.name}
                        className={cn(
                          'text-xl font-normal tracking-tight transition-colors duration-300',
                          isActive ? 'text-foreground' : ''
                        )}
                      />
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

        {/* Right (8 cols): Agent detail card */}
        <div className="lg:col-span-8 flex flex-col">
          <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
            Agent Detail
          </p>

          {/* Detail card — clean, no globe background */}
          <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <div className="relative overflow-hidden rounded-2xl border border-border/50 bg-card min-h-[380px]">
            {/* Animated agent content */}
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
                className="relative z-10 p-6 h-full"
              >
                {/* Agent header */}
                <div className="flex items-center gap-4 mb-6">
                  <div
                    className="w-14 h-14 rounded-xl flex items-center justify-center shrink-0"
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
                    <HyperText text={activeAgent.name} className="text-2xl font-bold text-foreground" />
                    <p className="text-xs text-muted-foreground">{activeAgent.role}</p>
                  </div>
                  <div className="ml-auto flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                    <span className="text-xs text-green-400 font-mono">running</span>
                  </div>
                </div>

                <p className="text-sm text-muted-foreground mb-6 leading-relaxed max-w-lg">
                  {activeAgent.description}
                </p>

                {/* Task list */}
                <div className="mb-6">
                  <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-3">
                    Responsibilities
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {activeAgent.tasks.map((task, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 text-xs text-muted-foreground bg-white/5 rounded-lg px-3 py-2.5 border border-border/30 transition-all duration-200 hover:bg-white/10"
                      >
                        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: activeAgent.color }} />
                        {task}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Recent events for this agent */}
                {agentEvents.length > 0 && (
                  <div>
                    <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-3">
                      Recent Activity
                    </p>
                    <div className="space-y-1.5">
                      {agentEvents.map((event: any, i) => (
                        <div key={i} className="text-xs text-muted-foreground bg-white/5 rounded-lg px-3 py-2 border border-border/20 truncate">
                          {event.message ?? event.type ?? JSON.stringify(event).slice(0, 80)}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
          </GlowCard>
        </div>
      </div>

      {/* ── LEAD GEOGRAPHY ────────────────────────────────────────── */}
      <div className="mt-8">
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Lead Geography
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
        <div className="rounded-2xl border border-border/50 bg-card p-6 flex flex-col items-center gap-4">
          <div>
            <p className="text-sm font-semibold text-foreground text-center">Global Lead Distribution</p>
            <p className="text-xs text-muted-foreground text-center mt-1">
              {GLOBE_MARKERS.length} active regions tracked by the pipeline
            </p>
          </div>
          <Globe
            className="w-[300px] h-[300px]"
            markers={GLOBE_MARKERS}
            dark={1}
            baseColor={[0.1, 0.1, 0.15]}
            markerColor={[0.35, 0.95, 0.8]}
            glowColor={[0.2, 0.8, 0.7]}
            mapBrightness={4}
            speed={0.003}
          />
          {/* Region labels */}
          <div className="flex flex-wrap gap-2 justify-center">
            {GLOBE_MARKERS.map((m) => (
              <span key={m.id} className="text-[10px] font-mono text-muted-foreground/60 bg-white/5 px-2 py-1 rounded-md border border-border/20">
                {m.label}
              </span>
            ))}
          </div>
        </div>
        </GlowCard>
      </div>

      {/* ── AGENT CHAT ────────────────────────────────────────────── */}
      <div className="mt-8">
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Agent Chat
        </p>
        {/* MessageDock — full width, anchored to bottom of content */}
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <MessageDock
            characters={dockCharacters}
            theme="dark"
            onMessageSend={handleMessageSend}
            placeholder={(name) => `Message ${name}...`}
            closeOnSend={false}
          />
        </GlowCard>
      </div>
    </div>
  )
}
