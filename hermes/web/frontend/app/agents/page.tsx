'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { AgentChat } from '@/components/agent-chat'

/**
 * Agents (/agents) — Daemon control
 * Communicate with agents, monitor tasks, review health.
 */
export default function AgentsPage() {
  const { token } = useToken()
  const { health, events } = useWarRoom()

  const agents = [
    { id: 'perseus', name: 'Perseus', role: 'CEO / Scheduler', status: 'running', color: '#58e0ff' },
    { id: 'titan', name: 'Titan', role: 'Revenue Engine', status: 'running', color: '#39f3e2' },
    { id: 'clawdbot', name: 'ClawdBot', role: 'Site Builder', status: 'running', color: '#62f1b5' },
    { id: 'hermes', name: 'Hermes', role: 'Alerts / Comms', status: 'running', color: '#ffb347' },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-2xl font-bold text-foreground">Agents</h1>
        <span className="text-xs text-muted-foreground">Daemon control — communicate, monitor, review</span>
      </div>

      {/* Agent status cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {agents.map((agent) => (
          <div key={agent.id} className="glass-card hud-panel rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <img
                src={`/assets/generated/agents/${agent.id}.svg`}
                alt={agent.name}
                className="w-8 h-8 rounded-full"
              />
              <div>
                <div className="text-sm font-medium text-foreground">{agent.name}</div>
                <div className="text-[10px] text-muted-foreground">{agent.role}</div>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green" />
              <span className="text-[10px] text-green">{agent.status}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Agent chat */}
      <AgentChat token={token} />
    </div>
  )
}
