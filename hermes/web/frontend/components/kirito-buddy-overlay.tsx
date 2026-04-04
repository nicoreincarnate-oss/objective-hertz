'use client'

import { useEffect, useMemo } from 'react'
import { Activity, ShieldAlert, ShieldCheck } from 'lucide-react'

import { useWarRoom } from '@/contexts/war-room-context'
import { deriveKiritoPresence } from '@/lib/kirito-presence'

function liveDaemonCount(daemons: Record<string, { last_heartbeat: string | null; paused: boolean }>) {
  return Object.values(daemons).filter((daemon) => {
    if (daemon.paused || !daemon.last_heartbeat) return false
    return Date.now() - new Date(daemon.last_heartbeat).getTime() < 120_000
  }).length
}

export function KiritoBuddyOverlay() {
  const { health, taskQueue, daemons, events } = useWarRoom()

  useEffect(() => {
    const previousBodyBackground = document.body.style.background
    const previousBodyImage = document.body.style.backgroundImage
    const previousHtmlBackground = document.documentElement.style.background

    document.body.style.background = 'transparent'
    document.body.style.backgroundImage = 'none'
    document.documentElement.style.background = 'transparent'

    return () => {
      document.body.style.background = previousBodyBackground
      document.body.style.backgroundImage = previousBodyImage
      document.documentElement.style.background = previousHtmlBackground
    }
  }, [])

  const highSignalEvents = useMemo(
    () =>
      events.filter((event) =>
        ['approval_required', 'pipeline_error', 'review_needed', 'daemon_restart', 'daemon_control'].includes(event.event_type),
      ),
    [events],
  )

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

  const topSignal = highSignalEvents[0]?.event_type?.replace(/[_-]+/g, ' ') ?? 'watching the lane'
  const pendingApprovals = health?.metrics.pending_approvals ?? 0

  return (
    <div className="relative min-h-screen overflow-hidden bg-transparent select-none" data-tauri-drag-region>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 top-4 flex items-end justify-center px-3">
        <div className="relative w-full max-w-[320px]">
          <div className="absolute inset-[10%_6%_12%] rounded-full bg-[radial-gradient(circle_at_50%_38%,rgba(92,246,255,0.28),rgba(72,159,255,0.12)_42%,transparent_72%)] blur-2xl" />
          <div className="absolute bottom-3 left-1/2 h-6 w-[58%] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(7,12,19,0.48),rgba(7,12,19,0))] blur-md" />

          <video
            autoPlay
            loop
            muted
            playsInline
            poster="/assets/kirito/kirito-poster.png"
            className="relative z-10 h-auto w-full object-contain drop-shadow-[0_26px_40px_rgba(0,0,0,0.26)] [mix-blend-mode:multiply]"
          >
            <source src="/assets/kirito/kirito-loop.mp4" type="video/mp4" />
          </video>
        </div>
      </div>

      <div className="absolute right-3 top-3 z-20 max-w-[220px] rounded-[20px] border border-cyan-300/25 bg-slate-950/72 p-3 text-cyan-50 shadow-[0_0_0_1px_rgba(88,224,255,0.12),0_18px_44px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-200">KIRITO // HERMES</p>
            <p className="mt-1 text-[11px] text-cyan-100/80">{presence.badgeLabel}</p>
          </div>
          {pendingApprovals > 0 ? (
            <ShieldAlert className="h-4 w-4 text-amber-300" />
          ) : (
            <ShieldCheck className="h-4 w-4 text-cyan-200" />
          )}
        </div>

        <p className="text-sm font-semibold leading-6 text-white">{presence.statusLine}</p>
        <p className="mt-2 text-xs leading-5 text-slate-200/82">{presence.guidanceLine}</p>

        <div className="mt-3 flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-[11px] text-slate-100/85">
          <div className="flex items-center gap-2">
            <Activity className="h-3.5 w-3.5 text-cyan-200" />
            <span>{topSignal}</span>
          </div>
          <span>{pendingApprovals} approvals</span>
        </div>
      </div>
    </div>
  )
}
