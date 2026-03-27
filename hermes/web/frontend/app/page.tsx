'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'
import { Status } from '@/components/ui/hud-status-1'
import { GlowCard } from '@/components/ui/spotlight-card'
import { RainbowButton } from '@/components/ui/rainbow-borders-button'
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

        {/* HUD Status badges — system-level connection + mode status (different from HeroCard metrics) */}
        <div className="flex items-center gap-2 flex-wrap mt-4">
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
      </div>

      {/* ── KEY METRICS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Key Metrics
        </p>
        {/* GlowCard wraps MetricsRow for the spotlight hover effect */}
        <GlowCard customSize glowColor="blue" className="w-full p-0">
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

      {/* ── LIVE EVENTS ───────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Live Events
        </p>
        <SignalLedger events={events ?? []} />
      </div>

      {/* ── QUICK ACTIONS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Quick Actions
        </p>
        <div className="flex items-center gap-3 flex-wrap">
          <RainbowButton onClick={() => {}} className="cursor-pointer transition-all duration-200">
            Approve All
          </RainbowButton>
          <RainbowButton onClick={() => {}} className="cursor-pointer transition-all duration-200">
            Run Pipeline
          </RainbowButton>
          <RainbowButton onClick={() => router.push('/pipeline')} className="cursor-pointer transition-all duration-200">
            Deploy Sites
          </RainbowButton>
          <RainbowButton onClick={() => router.push('/agents')} className="cursor-pointer transition-all duration-200">
            Message Agents
          </RainbowButton>
          <RainbowButton onClick={() => router.push('/intel')} className="cursor-pointer transition-all duration-200">
            Ask Intelligence
          </RainbowButton>
        </div>
      </div>
    </div>
  )
}
