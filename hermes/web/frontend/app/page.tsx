'use client'

import { useToken } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { SignalLedger } from '@/components/signal-ledger'
import { Status } from '@/components/ui/hud-status-1'
import { GlowCard } from '@/components/ui/spotlight-card'

/**
 * Command Center (/) — Operational HUD
 * Quick status, critical metrics, live alerts.
 * Agent Chat → /agents, Pipeline → /pipeline, Strategic View → /intel
 */

function RainbowButton({
  children,
  onClick,
  className = '',
}: {
  children: React.ReactNode
  onClick?: () => void
  className?: string
}) {
  return (
    <>
      <style>{`
        .rainbow-btn {
          position: relative;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          padding: 0 1.25rem;
          height: 2.5rem;
          background: #0a0a0f;
          border-radius: 0.75rem;
          border: none;
          color: white;
          cursor: pointer;
          font-weight: 700;
          font-size: 0.8125rem;
          letter-spacing: 0.04em;
          transition: opacity 0.2s, transform 0.15s;
          white-space: nowrap;
        }
        .rainbow-btn:hover { opacity: 0.9; transform: translateY(-1px); }
        .rainbow-btn:active { transform: translateY(0); }
        .rainbow-btn::before,
        .rainbow-btn::after {
          content: '';
          position: absolute;
          left: -2px;
          top: -2px;
          border-radius: 14px;
          background: linear-gradient(45deg,#fb0094,#0000ff,#00ff00,#ffff00,#ff0000,#fb0094,#0000ff,#00ff00,#ffff00,#ff0000);
          background-size: 400%;
          width: calc(100% + 4px);
          height: calc(100% + 4px);
          z-index: -1;
          animation: rainbow-anim 20s linear infinite;
        }
        .rainbow-btn::after { filter: blur(8px); }
        @keyframes rainbow-anim {
          0%   { background-position: 0 0; }
          50%  { background-position: 400% 0; }
          100% { background-position: 0 0; }
        }
      `}</style>
      <button className={`rainbow-btn ${className}`} onClick={onClick}>
        {children}
      </button>
    </>
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

  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = leads?.length ?? 0
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0

  const isLive = connectionStatus === 'connected'
  const mode = (health?.mode as 'review' | 'autonomous') || 'review'

  return (
    <div className="space-y-6">
      {/* Status badges row */}
      <div className="flex items-center gap-3 flex-wrap">
        <Status
          variant={isLive ? 'primary' : 'warning'}
          text={isLive ? 'LIVE' : 'OFFLINE'}
        />
        <Status
          variant={mode === 'autonomous' ? 'primary' : 'secondary'}
          text={mode === 'autonomous' ? 'AUTONOMOUS' : 'REVIEW MODE'}
        />
        {(health?.metrics?.pending_approvals ?? 0) > 0 && (
          <Status
            variant="warning"
            text={`${health?.metrics?.pending_approvals} PENDING`}
          />
        )}
      </div>

      {/* Hero */}
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

      {/* Quick actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <RainbowButton onClick={() => {}}>Approve All</RainbowButton>
        <RainbowButton onClick={() => {}}>Run Pipeline</RainbowButton>
        <RainbowButton onClick={() => {}}>Deploy Sites</RainbowButton>
      </div>

      {/* Metrics — each card wrapped in GlowCard spotlight */}
      <GlowCard glowColor="blue" customSize className="w-full p-0 bg-transparent border-0 shadow-none">
        <MetricsRow
          revenueCleared={revenueCleared}
          pendingRevenue={pendingRevenue}
          emailsToday={health?.metrics?.emails_sent_today || 0}
          emailsWeek={0}
          closeRate={closeRate}
          metricHistory={metricHistory}
        />
      </GlowCard>

      {/* Signal Ledger — live events */}
      <SignalLedger events={events ?? []} />
    </div>
  )
}
