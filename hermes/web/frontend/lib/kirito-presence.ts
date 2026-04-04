export interface KiritoEvent {
  event_type: string
  payload?: Record<string, unknown>
}

export interface KiritoSignal {
  mode: string
  pendingApprovals: number
  blockedTasks: number
  activeTasks: number
  liveDaemons: number
  totalDaemons: number
  highSignalEvents: KiritoEvent[]
}

export interface KiritoPresence {
  mood: 'alert' | 'focused' | 'steady'
  badgeLabel: string
  statusLine: string
  guidanceLine: string
  accentClass: string
}

function humanizeEvent(eventType: string): string {
  return eventType.replace(/[_-]+/g, ' ').toLowerCase()
}

function summarizeEvent(event: KiritoEvent | undefined): string | null {
  if (!event) return null

  const explicitLead = event.payload?.lead
  if (typeof explicitLead === 'string' && explicitLead.trim()) {
    return explicitLead
  }

  const reason = event.payload?.reason
  if (typeof reason === 'string' && reason.trim()) {
    return reason
  }

  return humanizeEvent(event.event_type)
}

export function deriveKiritoPresence(signal: KiritoSignal): KiritoPresence {
  const topEvent = summarizeEvent(signal.highSignalEvents[0])

  if (signal.blockedTasks > 0 || signal.pendingApprovals >= 3) {
    return {
      mood: 'alert',
      badgeLabel: 'Operator Link Required',
      statusLine: `${signal.pendingApprovals} approvals and ${signal.blockedTasks} blocked tasks are waiting for direction.`,
      guidanceLine: topEvent
        ? `Clear the approval queue first, then resolve ${topEvent}.`
        : 'Clear the approval queue first, then unblock the stalled tasks.',
      accentClass: 'from-red-500/30 via-orange-400/25 to-amber-300/20',
    }
  }

  if (signal.mode === 'autonomous' || signal.activeTasks >= 2) {
    return {
      mood: 'focused',
      badgeLabel: 'Autonomous Sweep',
      statusLine: `${signal.activeTasks} active tasks across ${signal.liveDaemons}/${signal.totalDaemons || 1} live daemons.`,
      guidanceLine: topEvent
        ? `Monitor ${topEvent} while Hermes keeps the lane moving.`
        : 'Monitor the live sweep and be ready for the next approval gate.',
      accentClass: 'from-cyan-400/30 via-sky-400/20 to-emerald-300/20',
    }
  }

  return {
    mood: 'steady',
    badgeLabel: 'Standby Watch',
    statusLine: `Kirito is idling with ${signal.liveDaemons}/${signal.totalDaemons || 1} daemons live and ${signal.pendingApprovals} approvals pending.`,
    guidanceLine: topEvent
      ? `Light watch only. Keep an eye on ${topEvent}.`
      : 'Light watch only. The system is stable enough for companion mode.',
    accentClass: 'from-violet-400/20 via-cyan-300/20 to-slate-200/10',
  }
}
