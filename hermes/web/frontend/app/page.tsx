'use client'

import { motion } from 'framer-motion'
import { useToken } from '@/hooks/use-token'
import { WarRoomProvider, useWarRoom } from '@/contexts/war-room-context'
import { HeroCard } from '@/components/hero-card'
import { MetricsRow } from '@/components/metrics-row'
import { AgentChat } from '@/components/agent-chat'
import { CinematicBackdrop } from '@/components/cinematic-backdrop'
import { StrategicView } from '@/components/strategic-view'
import { PipelinePulse } from '@/components/pipeline-pulse'
import { SignalLedger } from '@/components/signal-ledger'
import { LeadsTable } from '@/components/leads-table'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { TokenInput } from '@/components/token-input'

function ConnectionIndicator() {
  const { connectionStatus, lastSyncTime } = useWarRoom()

  const dotClass =
    connectionStatus === 'connected'
      ? 'ws-dot ws-dot-connected'
      : connectionStatus === 'polling'
      ? 'ws-dot ws-dot-polling'
      : 'ws-dot ws-dot-disconnected'

  const label =
    connectionStatus === 'connected'
      ? 'Live'
      : connectionStatus === 'polling'
      ? 'Polling'
      : 'Offline'

  const syncTime = lastSyncTime
    ? lastSyncTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '--:--'

  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
      <span className={dotClass} />
      {label} • Last sync: {syncTime}
    </span>
  )
}

function WarRoomDashboard() {
  const { token } = useToken()
  const {
    health,
    pipeline,
    leads,
    events,
    healthStatus,
    isLoading,
    connectionStatus,
  } = useWarRoom()

  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = (leads?.length ?? 0)
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0

  const backendOffline = connectionStatus === 'disconnected' && !health

  return (
    <div className="relative min-h-screen bg-background aurora-bg noise-overlay overflow-hidden">
      <CinematicBackdrop />

      {/* Health-reactive aurora overlay */}
      <div
        className={`absolute inset-0 pointer-events-none transition-all duration-1000 ${
          healthStatus === 'green'
            ? 'aurora-health-green'
            : healthStatus === 'amber'
            ? 'aurora-health-amber'
            : 'aurora-health-red'
        }`}
      />

      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8">
        {backendOffline && (
          <section className="mb-6">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-card hud-panel rounded-xl p-4 flex items-start gap-3 border border-amber/30"
            >
              <AlertTriangle className="w-5 h-5 text-amber mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-foreground">Backend Status</p>
                <p className="text-sm text-muted-foreground">
                  Unable to reach Hermes backend. The War Room shell is live, but data modules are offline.
                </p>
              </div>
            </motion.div>
          </section>
        )}

        <section className="mb-6">
          <HeroCard
            mode={health?.mode || 'review'}
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
        </section>

        <section className="mb-6">
          <MetricsRow
            revenueCleared={revenueCleared}
            pendingRevenue={pendingRevenue}
            emailsToday={health?.metrics?.emails_sent_today || 0}
            emailsWeek={0}
            closeRate={closeRate}
          />
        </section>

        <div className="grid lg:grid-cols-2 gap-6 mb-6">
          <AgentChat token={token!} />
          {pipeline && Object.keys(pipeline).length > 0 && <PipelinePulse data={pipeline} />}
        </div>

        <section className="mb-6">
          <StrategicView token={token!} />
        </section>

        <section className="mb-6">
          {events && events.length > 0 && <SignalLedger events={events} />}
        </section>

        <section>
          {leads && leads.length > 0 && <LeadsTable leads={leads} />}
        </section>

        <footer className="mt-8 pt-6 border-t border-border flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            PERSEUS War Room • Autonomous AI Revenue System • v2.0.0
          </p>
          <ConnectionIndicator />
        </footer>
      </div>
    </div>
  )
}

export default function PerseusWarRoom() {
  const { token, isLoading: tokenLoading, setToken } = useToken()

  if (tokenLoading) {
    return (
      <div className="relative min-h-screen bg-background aurora-bg noise-overlay flex items-center justify-center overflow-hidden">
        <CinematicBackdrop priority />
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="relative z-10 flex flex-col items-center gap-4"
        >
          <div className="relative">
            <div className="absolute inset-0 bg-gold/30 blur-xl rounded-full" />
            <Loader2 className="w-12 h-12 text-gold animate-spin relative" />
          </div>
          <p className="text-muted-foreground text-sm">Initializing PERSEUS...</p>
        </motion.div>
      </div>
    )
  }

  if (!token) {
    return <TokenInput onTokenSubmit={setToken} />
  }

  return (
    <WarRoomProvider>
      <WarRoomDashboard />
    </WarRoomProvider>
  )
}
