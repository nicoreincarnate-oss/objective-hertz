'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { useRouter } from 'next/navigation'

/**
 * Command Center (/) — Operational HUD
 * Quick status, critical metrics, live alerts.
 * Agent Chat → /agents, Pipeline → /pipeline, Strategic View → /intel
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
  } = useWarRoom()
  const router = useRouter()

  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = leads?.length ?? 0
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0

  const isLive = connectionStatus === 'connected'
  const mode = (health?.mode as 'review' | 'autonomous') || 'review'

  const quickActions = [
    { label: 'Approve All', onClick: () => {} },
    { label: 'Run Pipeline', onClick: () => {} },
    { label: 'Deploy Sites', onClick: () => router.push('/pipeline') },
    { label: 'Message Agents', onClick: () => router.push('/agents') },
    { label: 'Ask Intelligence', onClick: () => router.push('/intel') },
  ]

  return (
    <div className="space-y-8">
      {/* ── SYSTEM STATUS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          System Status
        </p>

        {/* Hero card — operational metrics (emails, leads, closed deals) */}
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
        {/* GlowCard wraps MetricsRow for the spotlight hover effect */}
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <MetricsRow
            revenueCleared={revenueCleared}
            pendingRevenue={pendingRevenue}
            emailsToday={health?.metrics?.emails_sent_today || 0}
            emailsWeek={0}
            closeRate={closeRate}
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
