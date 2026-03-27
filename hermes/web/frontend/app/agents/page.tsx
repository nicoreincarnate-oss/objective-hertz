'use client'

import { useState } from 'react'
import { useToken, authHeaders } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { Globe } from '@/components/ui/cobe-globe'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { Send } from 'lucide-react'

/**
 * Agents (/agents) — Daemon control
 * Vertical tabs for agent selection, detail card, globe for lead geography,
 * and inline agent chat (replaces broken MessageDock).
 */


const AGENTS = [
  {
    id: 'perseus',
    name: 'Perseus',
    role: 'CEO / Scheduler',
    color: '#58e0ff',
    gradientColors: '#58e0ff, #0ea5e9',
    description: 'Top-level orchestrator. Schedules all daemon tasks, monitors system health, triggers pipeline runs, and self-audits via MAGMA.',
    tasks: ['Schedule pipeline runs', 'Health monitoring', 'MAGMA self-audit', 'Agent coordination'],
  },
  {
    id: 'titan',
    name: 'Titan',
    role: 'Revenue Engine',
    color: '#39f3e2',
    gradientColors: '#39f3e2, #10b981',
    description: '10-stage revenue pipeline. Ingests leads, enriches via Firecrawl, generates personalized outreach, and closes deals.',
    tasks: ['Lead ingestion', 'Data enrichment', 'Email generation', 'Response handling'],
  },
  {
    id: 'clawdbot',
    name: 'ClawdBot',
    role: 'Site Builder',
    color: '#62f1b5',
    gradientColors: '#62f1b5, #22c55e',
    description: 'Builds and deploys personalized demo sites for warm leads. Uses browser automation to capture screenshots and validate deployments.',
    tasks: ['Template selection', 'Content personalization', 'Netlify deployment', 'Screenshot capture'],
  },
  {
    id: 'hermes',
    name: 'Hermes',
    role: 'Alerts / Comms',
    color: '#ffb347',
    gradientColors: '#ffb347, #f97316',
    description: 'Real-time event listener and alert dispatcher. Sends Telegram notifications, powers this War Room dashboard via WebSocket.',
    tasks: ['Event monitoring', 'Telegram alerts', 'WebSocket sync', 'A2A relay'],
  },
]

export default function AgentsPage() {
  const { token } = useToken()
  const { health, events, daemons } = useWarRoom()
  const [activeIndex, setActiveIndex] = useState(0)
  const [direction, setDirection] = useState(0)
  const [chatInput, setChatInput] = useState('')

  const handleTabClick = (index: number) => {
    if (index === activeIndex) return
    setDirection(index > activeIndex ? 1 : -1)
    setActiveIndex(index)
  }

  const activeAgent = AGENTS[activeIndex]

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

  const [chatHistory, setChatHistory] = useState<Array<{ agent: string; message: string; time: Date }>>([])
  const [chatSending, setChatSending] = useState(false)

  const handleChatSend = async () => {
    if (!chatInput.trim() || !token || chatSending) return
    setChatSending(true)
    const msg = chatInput.trim()
    setChatInput('')

    try {
      const formData = new FormData()
      formData.set('target_agent', activeAgent.id)
      formData.set('priority', 'routine')
      formData.set('message', msg)

      const res = await fetch('/api/operator-chat', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })

      if (res.ok) {
        setChatHistory(prev => [...prev, { agent: activeAgent.id, message: msg, time: new Date() }])
      }
    } finally {
      setChatSending(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <HyperText text="Agents" className="text-2xl font-bold text-foreground" />
        <p className="text-xs text-muted-foreground mt-1">Daemon control — communicate, monitor, review</p>
      </div>

      <div className="grid lg:grid-cols-12 gap-6 lg:gap-8 items-start">
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
                        src={`/assets/generated/agents/${agent.id}.png`}
                        alt={agent.name}
                        className="w-7 h-7 rounded-full shrink-0"
                      />
                      <HyperText
                        text={agent.name}
                        className={cn(
                          'text-xl font-normal tracking-tight transition-colors duration-300',
                          isActive ? 'text-foreground' : ''
                        )}
                      />
                      {(() => {
                        const d = daemons[agent.id]
                        const hb = d?.last_heartbeat ? Date.now() - new Date(d.last_heartbeat).getTime() : Infinity
                        const color = d?.paused ? 'bg-amber-400' : hb < 60000 ? 'bg-green-400 animate-pulse' : hb < 300000 ? 'bg-amber-400' : 'bg-red-400'
                        return <span className={`w-1.5 h-1.5 rounded-full ${color}`} />
                      })()}
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
        <div className="lg:col-span-8 flex flex-col gap-8">
          <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em]">
            Agent Detail
          </p>

          <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
            <div className="relative overflow-hidden rounded-2xl border border-border/50 bg-card min-h-[380px]">
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
                  className="relative z-10 p-4 lg:p-8 h-full"
                >
                  {/* Agent header */}
                  <div className="flex items-center gap-4 mb-6">
                    <div
                      className="w-14 h-14 rounded-xl flex items-center justify-center shrink-0 overflow-hidden"
                      style={{ background: `linear-gradient(135deg, ${activeAgent.gradientColors})` }}
                    >
                      <img src={`/assets/generated/agents/${activeAgent.id}.png`} alt={activeAgent.name} className="w-10 h-10 rounded-lg" />
                    </div>
                    <div>
                      <HyperText text={activeAgent.name} className="text-2xl font-bold text-foreground" />
                      <p className="text-xs text-muted-foreground mt-0.5">{activeAgent.role}</p>
                    </div>
                    {(() => {
                      const d = daemons[activeAgent.id]
                      const hb = d?.last_heartbeat ? Date.now() - new Date(d.last_heartbeat).getTime() : Infinity
                      const isRunning = !d?.paused && hb < 60000
                      const isPaused = d?.paused
                      const color = isPaused ? 'text-amber-400' : isRunning ? 'text-green-400' : hb < 300000 ? 'text-amber-400' : 'text-red-400'
                      const dotColor = isPaused ? 'bg-amber-400' : isRunning ? 'bg-green-400 animate-pulse' : hb < 300000 ? 'bg-amber-400' : 'bg-red-400'
                      const label = isPaused ? 'paused' : isRunning ? 'running' : hb < 300000 ? 'slow' : 'offline'
                      return (
                        <div className="ml-auto flex items-center gap-1.5">
                          <span className={`w-2 h-2 rounded-full ${dotColor}`} />
                          <span className={`text-xs font-mono ${color}`}>{label}</span>
                        </div>
                      )
                    })()}
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
                          className="flex items-center gap-2.5 text-xs text-muted-foreground bg-white/5 rounded-lg px-4 py-3 border border-border/30 transition-all duration-200 hover:bg-white/10"
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
                          <div key={i} className="text-xs text-muted-foreground bg-white/5 rounded-lg px-4 py-2.5 border border-border/20 truncate">
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

          {/* Inline Agent Chat — replaces broken MessageDock */}
          <div>
            <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
              Message {activeAgent.name}
            </p>
            <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
              <div className="rounded-xl border border-border/50 bg-card p-4 space-y-3">
                {/* Chat history */}
                {chatHistory.filter(h => h.agent === activeAgent.id).length > 0 && (
                  <div className="space-y-2 max-h-32 overflow-y-auto">
                    {chatHistory.filter(h => h.agent === activeAgent.id).map((h, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <span className="text-[10px] text-muted-foreground/40 mt-0.5 shrink-0">
                          {h.time.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                        </span>
                        <span className="text-xs text-muted-foreground">{h.message}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex items-center gap-3">
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 overflow-hidden"
                  style={{ background: `linear-gradient(135deg, ${activeAgent.gradientColors})` }}
                >
                  <img src={`/assets/generated/agents/${activeAgent.id}.png`} alt={activeAgent.name} className="w-6 h-6" />
                </div>
                <input
                  type="text"
                  className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/40 outline-none"
                  placeholder={`Message ${activeAgent.name}...`}
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleChatSend()}
                />
                <button
                  onClick={handleChatSend}
                  disabled={!chatInput.trim()}
                  className="w-8 h-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center transition-all disabled:opacity-30 cursor-pointer"
                >
                  <Send className="w-3.5 h-3.5 text-muted-foreground" />
                </button>
                </div>
              </div>
            </GlowCard>
          </div>
        </div>
      </div>

      {/* ── LEAD GEOGRAPHY ────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Lead Geography
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <div className="rounded-2xl border border-border/50 bg-card p-4 lg:p-8 flex flex-col items-center gap-4">
            <div>
              <p className="text-sm font-semibold text-foreground text-center">Global Lead Distribution</p>
              <p className="text-xs text-muted-foreground text-center mt-1">
                Regions will populate as leads enter the pipeline
              </p>
            </div>
            <Globe
              className="w-[200px] h-[200px] lg:w-[300px] lg:h-[300px]"
              markers={[]}
              dark={1}
              baseColor={[0.1, 0.1, 0.15]}
              markerColor={[0.35, 0.95, 0.8]}
              glowColor={[0.2, 0.8, 0.7]}
              mapBrightness={4}
              speed={0.003}
            />
            <p className="text-[10px] text-muted-foreground/40">No lead data yet — connect pipeline to populate</p>
          </div>
        </GlowCard>
      </div>
    </div>
  )
}
