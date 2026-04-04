'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import {
  Activity,
  Bot,
  Mic,
  ShieldCheck,
  Sparkles,
  Swords,
  Waves,
} from 'lucide-react'

import { useWarRoom } from '@/contexts/war-room-context'
import { deriveKiritoPresence } from '@/lib/kirito-presence'
import { HyperText } from '@/components/ui/hyper-text'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { GlowCard } from '@/components/ui/spotlight-card'

function humanize(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

function eventSummary(event: { event_type: string; payload?: Record<string, unknown> }) {
  const lead = event.payload?.lead
  if (typeof lead === 'string' && lead.trim()) return lead

  const reason = event.payload?.reason
  if (typeof reason === 'string' && reason.trim()) return reason

  return humanize(event.event_type)
}

function liveDaemonCount(daemons: Record<string, { last_heartbeat: string | null; paused: boolean }>) {
  return Object.values(daemons).filter((daemon) => {
    if (daemon.paused || !daemon.last_heartbeat) return false
    return Date.now() - new Date(daemon.last_heartbeat).getTime() < 120_000
  }).length
}

export function KiritoCompanion() {
  const { health, taskQueue, daemons, events, connectionStatus } = useWarRoom()

  const highSignalEvents = useMemo(
    () => events.filter((event) =>
      ['approval_required', 'pipeline_error', 'review_needed', 'daemon_restart', 'daemon_control'].includes(event.event_type)
    ),
    [events],
  )

  const queueDepth = Object.values(taskQueue).reduce((sum, count) => sum + count, 0)
  const activeTasks = taskQueue.running ?? taskQueue.in_progress ?? 0
  const blockedTasks = taskQueue.failed ?? taskQueue.blocked ?? 0
  const liveDaemons = liveDaemonCount(daemons)
  const totalDaemons = Object.keys(daemons).length

  const presence = deriveKiritoPresence({
    mode: health?.mode ?? 'review',
    pendingApprovals: health?.metrics.pending_approvals ?? 0,
    blockedTasks,
    activeTasks,
    liveDaemons,
    totalDaemons,
    highSignalEvents,
  })

  const topSignals = highSignalEvents.slice(0, 3)

  return (
    <div className="space-y-8">
      <section className="relative overflow-hidden rounded-[30px] border border-cyan-400/15 bg-[linear-gradient(135deg,rgba(8,12,20,0.98),rgba(9,20,33,0.84)),radial-gradient(circle_at_top_left,rgba(88,224,255,0.18),transparent_34%)] p-6 sm:p-8">
        <div className={`absolute inset-0 bg-gradient-to-br ${presence.accentClass} opacity-70`} />
        <div className="relative z-10 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl space-y-4">
            <Badge variant="outline" className="border-cyan-400/30 bg-cyan-400/10 text-cyan-100">
              <Swords className="h-3.5 w-3.5" />
              Kirito Companion
            </Badge>
            <div>
              <HyperText text="Kirito" className="text-3xl font-bold text-white sm:text-4xl" />
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
                Hermes now has an on-screen identity in the War Room. This companion chamber uses your Kling
                Kirito clip as the first visual layer, while the live mood, guidance, and signal summaries are
                driven by the actual system state.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge className="border-white/10 bg-white/10 text-white">{presence.badgeLabel}</Badge>
            <Badge variant="outline" className="border-cyan-400/30 bg-cyan-400/10 text-cyan-100">
              Mode: {health?.mode ?? 'review'}
            </Badge>
            <Badge variant="outline" className="border-white/10 bg-black/20 text-slate-200">
              Link: {connectionStatus}
            </Badge>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <GlowCard customSize glowColor={presence.mood === 'alert' ? 'red' : 'blue'} className="min-h-[640px] w-full bg-transparent p-0 shadow-none">
          <div className="relative h-full overflow-hidden rounded-[30px] border border-cyan-400/15 bg-[linear-gradient(180deg,rgba(246,248,255,0.96),rgba(217,235,255,0.88)_30%,rgba(7,12,19,0.85)_100%)]">
            <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between px-6 py-5">
              <div>
                <p className="text-[11px] uppercase tracking-[0.22em] text-slate-600">Companion Chamber</p>
                <p className="mt-1 text-sm font-medium text-slate-900">Kling visual layer, Hermes telemetry core</p>
              </div>
              <div className="rounded-full border border-slate-300/70 bg-white/60 px-3 py-1 text-[11px] font-medium text-slate-700 backdrop-blur-md">
                Clip v1 · 10s loop
              </div>
            </div>

            <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.98),transparent_34%),radial-gradient(circle_at_bottom,rgba(74,168,255,0.18),transparent_26%)]" />
            <div className="absolute inset-x-0 bottom-0 h-48 bg-gradient-to-t from-[#04080c] via-[#04080c]/72 to-transparent" />

            <div className="absolute inset-0 flex items-center justify-center px-4 pt-20">
              <video
                autoPlay
                loop
                muted
                playsInline
                poster="/assets/kirito/kirito-poster.png"
                className="h-full max-h-[540px] w-full object-contain drop-shadow-[0_28px_40px_rgba(0,0,0,0.28)]"
              >
                <source src="/assets/kirito/kirito-loop.mp4" type="video/mp4" />
              </video>
            </div>

            <div className="absolute inset-x-0 bottom-0 z-20 p-5 sm:p-6">
              <div className="rounded-[26px] border border-cyan-400/20 bg-slate-950/72 p-5 shadow-2xl backdrop-blur-xl">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-2">
                    <Badge variant="outline" className="border-cyan-400/25 bg-cyan-400/10 text-cyan-100">
                      Hermes Status Voice
                    </Badge>
                    <p className="max-w-2xl text-lg font-semibold leading-7 text-white">
                      {presence.statusLine}
                    </p>
                    <p className="text-sm leading-6 text-slate-300">{presence.guidanceLine}</p>
                  </div>
                  <div className="hidden rounded-2xl border border-cyan-400/20 bg-cyan-400/10 p-3 text-cyan-100 sm:block">
                    <Sparkles className="h-5 w-5" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </GlowCard>

        <div className="space-y-6">
          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <CardTitle className="text-lg text-foreground">Live Presence</CardTitle>
              <CardDescription>Kirito reacts to the same runtime state as the rest of the War Room.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 p-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/40 bg-background/50 p-4">
                <div className="flex items-center gap-2 text-cyan-200">
                  <Activity className="h-4 w-4" />
                  <span className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Queue</span>
                </div>
                <div className="mt-2 text-3xl font-semibold text-foreground">{queueDepth}</div>
                <p className="mt-1 text-xs text-muted-foreground">Tasks currently tracked by Hermes.</p>
              </div>

              <div className="rounded-2xl border border-border/40 bg-background/50 p-4">
                <div className="flex items-center gap-2 text-cyan-200">
                  <Bot className="h-4 w-4" />
                  <span className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Daemons</span>
                </div>
                <div className="mt-2 text-3xl font-semibold text-foreground">
                  {liveDaemons}/{totalDaemons || 0}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Live heartbeat coverage in the fleet.</p>
              </div>

              <div className="rounded-2xl border border-border/40 bg-background/50 p-4">
                <div className="flex items-center gap-2 text-cyan-200">
                  <ShieldCheck className="h-4 w-4" />
                  <span className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Approvals</span>
                </div>
                <div className="mt-2 text-3xl font-semibold text-foreground">
                  {health?.metrics.pending_approvals ?? 0}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Governance gates still waiting on us.</p>
              </div>

              <div className="rounded-2xl border border-border/40 bg-background/50 p-4">
                <div className="flex items-center gap-2 text-cyan-200">
                  <Waves className="h-4 w-4" />
                  <span className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Voice Lane</span>
                </div>
                <div className="mt-2 text-xl font-semibold text-foreground">Pending</div>
                <p className="mt-1 text-xs text-muted-foreground">ElevenLabs binding is the next step after visual presence.</p>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <CardTitle className="text-lg text-foreground">Signal Feed</CardTitle>
              <CardDescription>The events Kirito should be reacting to right now.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              {topSignals.length === 0 && (
                <div className="rounded-2xl border border-dashed border-border/40 bg-background/35 px-4 py-5 text-sm text-muted-foreground">
                  Quiet lane. Kirito is in standby watch until the next significant event.
                </div>
              )}

              {topSignals.map((event) => (
                <div key={event.id} className="rounded-2xl border border-border/40 bg-background/50 p-4">
                  <p className="text-sm font-medium text-foreground">{humanize(event.event_type)}</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">{eventSummary(event)}</p>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <CardTitle className="text-lg text-foreground">Next Moves</CardTitle>
              <CardDescription>Best routes to deepen Kirito from here.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              <div className="rounded-2xl border border-border/40 bg-background/50 p-4 text-sm text-muted-foreground">
                Current visual source is the Kling clip in a contained stage. We can replace this later with keyed
                alpha footage or a true VRM pipeline without changing the surrounding companion system.
              </div>
              <div className="flex flex-wrap gap-3">
                <Button asChild className="bg-cyan-400 text-slate-950 hover:bg-cyan-300">
                  <Link href="/autopilot">Open Autopilot</Link>
                </Button>
                <Button asChild variant="outline" className="border-border/50 bg-background/50">
                  <Link href="/agents">Message Agents</Link>
                </Button>
                <Button asChild variant="outline" className="border-border/50 bg-background/50">
                  <Link href="/intel">Prep Voice Layer</Link>
                </Button>
              </div>
              <div className="rounded-2xl border border-cyan-400/15 bg-cyan-400/8 p-4 text-sm text-cyan-50">
                <div className="flex items-center gap-2">
                  <Mic className="h-4 w-4" />
                  <span className="font-medium">Voice bridge next</span>
                </div>
                <p className="mt-2 leading-6 text-cyan-100/85">
                  The companion shell is ready for ElevenLabs speech output and buddy-window extraction after this.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </section>
    </div>
  )
}
