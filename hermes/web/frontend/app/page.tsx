'use client'

import { useEffect, useState } from 'react'
import useSWR from 'swr'
import { motion } from 'framer-motion'
import { useToken } from '@/hooks/use-token'
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

const fetcher = (url: string) => fetch(url).then(res => res.json())

interface HealthData {
  error?: string
  status: string
  db_ok: boolean
  mode: 'review' | 'autonomous'
  agents: Record<string, { status: string; last_heartbeat: string }>
  metrics: {
    emails_sent_today: number
    emails_sent_week: number
    warm_leads: number
    sales_closed: number
    pending_approvals: number
    revenue_cleared: number
    revenue_pending: number
    total_leads: number
  }
}

interface PipelineData {
  discovered: number
  researched: number
  email_sent: number
  followed_up: number
  replied: number
  interested: number
  demo_built: number
  proposal_sent: number
  closed: number
  building: number
  deployed: number
  invoiced: number
  paid: number
}

interface Lead {
  id: string
  business_name: string
  email: string
  industry: string
  status: string
  lead_score: number
  created_at: string
}

interface Event {
  id: string
  event_type: string
  payload: Record<string, unknown>
  created_at: string
  acknowledged: boolean
}

export default function PerseusWarRoom() {
  const { token, isLoading: tokenLoading } = useToken()
  const [lastSync, setLastSync] = useState('--:--')

  // Auto-refresh every 30 seconds
  const refreshInterval = 30000

  const { data: health, error: healthFetchError, isLoading: healthLoading } = useSWR<HealthData>(
    token ? `/api/health?token=${token}` : null,
    fetcher,
    { refreshInterval }
  )

  const { data: pipeline } = useSWR<PipelineData>(
    token ? `/api/pipeline?token=${token}` : null,
    fetcher,
    { refreshInterval }
  )

  const { data: leads } = useSWR<Lead[]>(
    token ? `/api/leads?token=${token}` : null,
    fetcher,
    { refreshInterval }
  )

  const { data: events } = useSWR<Event[]>(
    token ? `/api/events?token=${token}` : null,
    fetcher,
    { refreshInterval }
  )

  // Update last sync time
  useEffect(() => {
    const updateSync = () => {
      const now = new Date()
      setLastSync(now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }))
    }
    
    if (health) {
      updateSync()
    }
    
    const interval = setInterval(updateSync, refreshInterval)
    return () => clearInterval(interval)
  }, [health])

  // Show loading while checking for token
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

  // Token required screen with input form
  if (!token) {
    return <TokenInput />
  }

  // Real metrics from backend — no invented numbers
  const revenueCleared = health?.metrics?.revenue_cleared ?? 0
  const pendingRevenue = health?.metrics?.revenue_pending ?? 0
  const totalLeads = health?.metrics?.total_leads ?? 0
  const closedLeads = health?.metrics?.sales_closed ?? 0
  const closeRate = totalLeads > 0 ? (closedLeads / totalLeads) * 100 : 0
  const backendStatusMessage = healthFetchError
    ? 'Unable to reach Hermes backend on port 8500. The Jarvis War Room shell is live, but data modules are offline.'
    : health?.error
    ? `${health.error}. The Jarvis War Room shell is live, but data modules are offline.`
    : healthLoading
    ? 'Connecting to Hermes backend...'
    : ''

  return (
    <div className="relative min-h-screen bg-background aurora-bg noise-overlay overflow-hidden">
      <CinematicBackdrop />
      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8">
        {backendStatusMessage && (
          <section className="mb-6">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-card hud-panel rounded-xl p-4 flex items-start gap-3 border border-amber/30"
            >
              <AlertTriangle className="w-5 h-5 text-amber mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-foreground">Backend Status</p>
                <p className="text-sm text-muted-foreground">{backendStatusMessage}</p>
              </div>
            </motion.div>
          </section>
        )}

        {/* Hero Card */}
        <section className="mb-6">
          <HeroCard
            mode={health?.mode || 'review'}
            pendingApprovals={health?.metrics?.pending_approvals || 0}
            emailsSent={health?.metrics?.emails_sent_today || 0}
            warmLeads={health?.metrics?.warm_leads || 0}
            salesClosed={health?.metrics?.sales_closed || 0}
            lastSync={lastSync}
          />
        </section>

        {/* Metrics Row */}
        <section className="mb-6">
          <MetricsRow
            revenueCleared={revenueCleared}
            pendingRevenue={pendingRevenue}
            emailsToday={health?.metrics?.emails_sent_today || 0}
            emailsWeek={health?.metrics?.emails_sent_week || 0}
            closeRate={closeRate}
          />
        </section>

        {/* Two Column Layout */}
        <div className="grid lg:grid-cols-2 gap-6 mb-6">
          {/* Agent Chat */}
          <AgentChat token={token} />

          {/* Pipeline Pulse */}
          {pipeline && <PipelinePulse data={pipeline} />}
        </div>

        <section className="mb-6">
          <StrategicView token={token} />
        </section>

        {/* Signal Ledger */}
        <section className="mb-6">
          {events && <SignalLedger events={events} />}
        </section>

        {/* Leads Table */}
        <section>
          {leads && <LeadsTable leads={leads} />}
        </section>

        {/* Footer */}
        <footer className="mt-8 pt-6 border-t border-border text-center">
          <p className="text-xs text-muted-foreground">
            PERSEUS War Room • Autonomous AI Revenue System • v1.0.0
          </p>
        </footer>
      </div>
    </div>
  )
}
