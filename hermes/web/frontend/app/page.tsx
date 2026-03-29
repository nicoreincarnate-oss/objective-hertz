'use client'

import { useState } from 'react'
import { useToken, authHeaders } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'
import { DaemonStatusPanel } from '@/components/daemon-status-panel'
import { ConfigPanel } from '@/components/config-panel'
import { BudgetCard } from '@/components/budget-card'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { useRouter } from 'next/navigation'

/**
 * Command Center (/) — Operational HUD + Control Plane
 * Status, metrics, daemon control, config, budget, events.
 */

export default function CommandCenter() {
  const { token } = useToken()
  const {
    health,
    pipeline,
    leads,
    events,
    healthStatus,
    connectionStatus,
    metricHistory,
    budget,
  } = useWarRoom()
  const router = useRouter()
  const [actionFeedback, setActionFeedback] = useState<string | null>(null)

  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = leads?.length ?? 0
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0

  const isLive = connectionStatus === 'connected'
  const mode = (health?.mode as 'review' | 'autonomous') || 'review'

  const showFeedback = (msg: string) => {
    setActionFeedback(msg)
    setTimeout(() => setActionFeedback(null), 3000)
  }

  const handleApproveAll = async () => {
    if (!token) return
    try {
      const res = await fetch('/api/review/bulk', {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'approve_all' }),
      })
      const data = await res.json()
      showFeedback(`Approved ${data.affected ?? 0} items`)
    } catch {
      showFeedback('Approve failed')
    }
  }

  const handleRunPipeline = async () => {
    if (!token) return
    try {
      const res = await fetch('/api/pipeline/run', {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: '{}',
      })
      const data = await res.json()
      showFeedback(data.task_id ? `Pipeline queued (#${data.task_id})` : 'Pipeline triggered')
    } catch {
      showFeedback('Pipeline trigger failed')
    }
  }

  const quickActions = [
    { label: 'Approve All', onClick: handleApproveAll },
    { label: 'Run Pipeline', onClick: handleRunPipeline },
    { label: 'Deploy Sites', onClick: () => router.push('/pipeline') },
    { label: 'Message Agents', onClick: () => router.push('/agents') },
    { label: 'Ask Intelligence', onClick: () => router.push('/intel') },
  ]

  return (
    <div className="space-y-10">
      {/* Action feedback toast */}
      {actionFeedback && (
        <div className="fixed top-20 right-6 z-50 bg-gold/90 text-background px-4 py-2 rounded-lg text-sm font-medium shadow-lg animate-in fade-in slide-in-from-top-2">
          {actionFeedback}
        </div>
      )}

      {/* ── SYSTEM STATUS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          System Status
        </p>
        <HeroCard
          mode={mode}
          pendingApprovals={health?.metrics?.pending_approvals || 0}
          emailsSent={health?.metrics?.emails_sent_today || 0}
          warmLeads={health?.metrics?.warm_leads || 0}
          salesClosed={health?.metrics?.sales_closed || 0}
          lastSync={
            isLive
              ? 'LIVE'
              : new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
          }
        />
      </div>

      {/* ── KEY METRICS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Key Metrics
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <MetricsRow
            revenueCleared={revenueCleared}
            pendingRevenue={pendingRevenue}
            emailsToday={health?.metrics?.emails_sent_today || 0}
            emailsWeek={0}
            closeRate={closeRate}
            budgetRemaining={budget.remaining}
            budgetTotal={budget.remaining + budget.total_spent || 800}
            metricHistory={metricHistory}
          />
        </GlowCard>
      </div>

      {/* ── QUICK ACTIONS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Quick Actions
        </p>
        <div className="flex items-center gap-3 flex-wrap">
          {quickActions.map(({ label, onClick }) => (
            <GlowCard
              key={label}
              customSize
              glowColor="blue"
              className="p-0 bg-transparent border-0 shadow-none"
            >
              <button
                onClick={onClick}
                className="glass-card hud-panel rounded-xl px-5 py-3 cursor-pointer transition-all duration-200 hover:bg-white/10 border border-border/50 text-sm font-medium text-foreground"
              >
                <HyperText text={label} className="text-sm font-medium" />
              </button>
            </GlowCard>
          ))}
        </div>
      </div>

      {/* ── DAEMON CONTROL ────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Daemon Control
        </p>
        <DaemonStatusPanel />
      </div>

      {/* ── CONFIGURATION ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Configuration
        </p>
        <ConfigPanel />
      </div>

      {/* ── BUDGET ────────────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Budget
        </p>
        <BudgetCard />
      </div>

      {/* ── LIVE EVENTS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Live Events
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <SignalLedger events={events ?? []} />
        </GlowCard>
      </div>
    </div>
  )
}
