'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'
import { Status } from '@/components/ui/hud-status-1'
import { useRouter } from 'next/navigation'

/**
 * Command Center (/) — Operational HUD
 * Quick status, critical metrics, live alerts.
 * Agent Chat → /agents, Pipeline → /pipeline, Strategic View → /intel
 */

function QuickActionButton({
  children,
  onClick,
  variant = 'default',
}: {
  children: React.ReactNode
  onClick?: () => void
  variant?: 'default' | 'accent'
}) {
  return (
    <button
      onClick={onClick}
      className={`
        inline-flex items-center justify-center gap-2 px-4 h-10 rounded-xl
        text-xs font-semibold tracking-wide transition-all duration-200
        border hover:translate-y-[-1px] active:translate-y-0
        ${variant === 'accent'
          ? 'bg-gold/10 border-gold/30 text-gold hover:bg-gold/15 hover:border-gold/50'
          : 'bg-white/5 border-border/60 text-muted-foreground hover:text-foreground hover:bg-white/8 hover:border-border'
        }
      `}
    >
      {children}
    </button>
  )
}

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

  return (
    <div className="space-y-6">
      {/* ── SYSTEM STATUS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-widest mb-3">
          System Status
        </p>
        {/* Hero card — HUD status badges embedded as header row */}
        <div className="relative">
          {/* Status badges float above the hero card at top-left */}
          <div className="absolute top-3 right-4 z-20 flex items-center gap-2 flex-wrap justify-end">
            <Status
              variant={isLive ? 'primary' : 'warning'}
              text={isLive ? 'LIVE' : 'OFFLINE'}
              scale={0.75}
            />
            <Status
              variant={mode === 'autonomous' ? 'primary' : 'secondary'}
              text={mode === 'autonomous' ? 'AUTONOMOUS' : 'REVIEW MODE'}
              scale={0.75}
            />
            {(health?.metrics?.pending_approvals ?? 0) > 0 && (
              <Status
                variant="warning"
                text={`${health?.metrics?.pending_approvals} PENDING`}
                scale={0.75}
              />
            )}
          </div>

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
      </div>

      {/* ── KEY METRICS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-widest mb-3">
          Key Metrics
        </p>
        <MetricsRow
          revenueCleared={revenueCleared}
          pendingRevenue={pendingRevenue}
          emailsToday={health?.metrics?.emails_sent_today || 0}
          emailsWeek={0}
          closeRate={closeRate}
          metricHistory={metricHistory}
        />
      </div>

      {/* ── LIVE EVENTS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-widest mb-3">
          Live Events
        </p>
        <SignalLedger events={events ?? []} />
      </div>

      {/* ── QUICK ACTIONS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-widest mb-3">
          Quick Actions
        </p>
        <div className="flex items-center gap-3 flex-wrap">
          <QuickActionButton variant="accent" onClick={() => {}}>
            Approve All
          </QuickActionButton>
          <QuickActionButton onClick={() => {}}>
            Run Pipeline
          </QuickActionButton>
          <QuickActionButton onClick={() => router.push('/pipeline')}>
            Deploy Sites
          </QuickActionButton>
          <QuickActionButton onClick={() => router.push('/agents')}>
            Message Agents
          </QuickActionButton>
          <QuickActionButton onClick={() => router.push('/intel')}>
            Ask Intelligence
          </QuickActionButton>
        </div>
      </div>
    </div>
  )
}
