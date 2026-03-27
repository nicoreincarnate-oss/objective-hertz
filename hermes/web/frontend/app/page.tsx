'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'

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

  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = leads?.length ?? 0
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0

  return (
    <div className="space-y-6">
      {/* Hero */}
      <HeroCard
        mode={(health?.mode as 'review' | 'autonomous') || 'review'}
        pendingApprovals={health?.metrics?.pending_approvals || 0}
        emailsSent={health?.metrics?.emails_sent_today || 0}
        warmLeads={health?.metrics?.warm_leads || 0}
        salesClosed={health?.metrics?.sales_closed || 0}
        lastSync={
          connectionStatus === 'connected'
            ? 'LIVE'
            : new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
        }
      />

      {/* Metrics */}
      <MetricsRow
        revenueCleared={revenueCleared}
        pendingRevenue={pendingRevenue}
        emailsToday={health?.metrics?.emails_sent_today || 0}
        emailsWeek={0}
        closeRate={closeRate}
        metricHistory={metricHistory}
      />

      {/* Signal Ledger — live events */}
      <SignalLedger events={events ?? []} />
    </div>
  )
}
