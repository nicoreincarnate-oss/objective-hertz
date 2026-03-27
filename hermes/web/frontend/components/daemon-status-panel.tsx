'use client'

import { useState } from 'react'
import { useWarRoom } from '@/contexts/war-room-context'
import { useToken, authHeaders } from '@/hooks/use-token'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { Pause, Play } from 'lucide-react'

const DAEMON_META: Record<string, { label: string; color: string; icon: string }> = {
  perseus: { label: 'Perseus', color: '#58e0ff', icon: '/assets/generated/agents/perseus.png' },
  titan: { label: 'Titan', color: '#39f3e2', icon: '/assets/generated/agents/titan.png' },
  clawdbot: { label: 'ClawdBot', color: '#62f1b5', icon: '/assets/generated/agents/clawdbot.png' },
  hermes: { label: 'Hermes', color: '#ffb347', icon: '/assets/generated/agents/hermes.png' },
}

function getHeartbeatStatus(lastHeartbeat: string | null): 'green' | 'amber' | 'red' {
  if (!lastHeartbeat) return 'red'
  const diff = Date.now() - new Date(lastHeartbeat).getTime()
  if (diff < 60_000) return 'green'
  if (diff < 300_000) return 'amber'
  return 'red'
}

export function DaemonStatusPanel() {
  const { daemons } = useWarRoom()
  const { token } = useToken()
  const [loading, setLoading] = useState<string | null>(null)

  const handleAction = async (name: string, action: 'pause' | 'resume') => {
    if (!token) return
    setLoading(name)
    try {
      await fetch(`/api/daemons/${name}/action`, {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
    } finally {
      setLoading(null)
    }
  }

  const daemonNames = Object.keys(DAEMON_META)

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {daemonNames.map((name) => {
        const meta = DAEMON_META[name]
        const daemon = daemons[name]
        const heartbeat = daemon ? getHeartbeatStatus(daemon.last_heartbeat) : 'red'
        const paused = daemon?.paused ?? false
        const isLoading = loading === name

        return (
          <GlowCard key={name} customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
            <div className="glass-card hud-panel rounded-xl p-6">
              <div className="flex items-center gap-3 mb-3">
                <img src={meta.icon} alt={meta.label} className="w-8 h-8 rounded-lg" />
                <div className="flex-1 min-w-0">
                  <HyperText text={meta.label} className="text-sm font-semibold text-foreground" />
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className={`w-2 h-2 rounded-full ${
                      heartbeat === 'green' ? 'bg-green-400' :
                      heartbeat === 'amber' ? 'bg-amber-400' : 'bg-red-400'
                    }`} />
                    <span className="text-[10px] text-muted-foreground">
                      {paused ? 'Paused' : heartbeat === 'green' ? 'Running' : heartbeat === 'amber' ? 'Slow' : 'Offline'}
                    </span>
                  </div>
                </div>
              </div>

              <button
                onClick={() => handleAction(name, paused ? 'resume' : 'pause')}
                disabled={isLoading}
                className={`w-full flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium cursor-pointer transition-all ${
                  paused
                    ? 'bg-green-500/10 text-green-400 hover:bg-green-500/20 border border-green-500/20'
                    : 'bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/20'
                } disabled:opacity-50`}
              >
                {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                {isLoading ? '...' : paused ? 'Resume' : 'Pause'}
              </button>
            </div>
          </GlowCard>
        )
      })}
    </div>
  )
}
