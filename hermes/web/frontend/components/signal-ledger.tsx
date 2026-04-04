'use client'

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity, Mail, Star, DollarSign, AlertTriangle, AlertCircle,
  MessageSquare, ArrowRight, Bot, Globe, FileText, CreditCard,
  Zap, Filter, X,
} from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { AnimatedList } from '@/components/ui/animated-list'

interface Event {
  id: number | string
  event_type: string
  payload: Record<string, unknown>
  created_at: string | null
  acknowledged: boolean
}

interface SignalLedgerProps {
  events: Event[]
}

type Severity = 'critical' | 'warning' | 'info' | 'success'

const EVENT_CONFIG: Record<string, { icon: React.ReactNode; severity: Severity; label: string }> = {
  email_sent:             { icon: <Mail className="w-3.5 h-3.5" />,          severity: 'info',     label: 'Email Sent' },
  emails_sent:            { icon: <Mail className="w-3.5 h-3.5" />,          severity: 'info',     label: 'Emails Sent' },
  email_queued:           { icon: <Mail className="w-3.5 h-3.5" />,          severity: 'info',     label: 'Email Queued' },
  lead_scored:            { icon: <Star className="w-3.5 h-3.5" />,          severity: 'info',     label: 'Lead Scored' },
  lead_approved:          { icon: <Zap className="w-3.5 h-3.5" />,           severity: 'success',  label: 'Lead Approved' },
  lead_rejected:          { icon: <X className="w-3.5 h-3.5" />,             severity: 'warning',  label: 'Lead Rejected' },
  lead_escalated:         { icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning',  label: 'Lead Escalated' },
  demo_completed:         { icon: <Globe className="w-3.5 h-3.5" />,         severity: 'success',  label: 'Demo Completed' },
  deal_closed:            { icon: <DollarSign className="w-3.5 h-3.5" />,    severity: 'success',  label: 'Deal Closed' },
  site_deployed:          { icon: <Globe className="w-3.5 h-3.5" />,         severity: 'success',  label: 'Site Deployed' },
  review_needed:          { icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning',  label: 'Review Needed' },
  approval_required:      { icon: <AlertCircle className="w-3.5 h-3.5" />,   severity: 'critical', label: 'Approval Required' },
  leads_discovered:       { icon: <ArrowRight className="w-3.5 h-3.5" />,    severity: 'info',     label: 'Leads Discovered' },
  operator_message_sent:  { icon: <MessageSquare className="w-3.5 h-3.5" />, severity: 'info',     label: 'Operator Message' },
  agent_message_ack:      { icon: <Bot className="w-3.5 h-3.5" />,           severity: 'info',     label: 'Agent Acknowledged' },
  kirito_command_received:{ icon: <MessageSquare className="w-3.5 h-3.5" />, severity: 'info',     label: 'Kirito Intake' },
  kirito_command_classified:{ icon: <Bot className="w-3.5 h-3.5" />,         severity: 'info',     label: 'Kirito Routed' },
  kirito_command_dispatching:{ icon: <ArrowRight className="w-3.5 h-3.5" />, severity: 'info',     label: 'Kirito Dispatching' },
  kirito_command_dispatched:{ icon: <ArrowRight className="w-3.5 h-3.5" />,  severity: 'info',     label: 'Kirito Dispatched' },
  kirito_command_local_action:{ icon: <Globe className="w-3.5 h-3.5" />,     severity: 'info',     label: 'Kirito Local Action' },
  kirito_command_queued:   { icon: <Activity className="w-3.5 h-3.5" />,      severity: 'info',     label: 'Kirito Queued' },
  kirito_command_running:  { icon: <Activity className="w-3.5 h-3.5" />,      severity: 'info',     label: 'Kirito Running' },
  kirito_command_awaiting_followup:{ icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning', label: 'Kirito Awaiting Follow-up' },
  kirito_command_failed:   { icon: <AlertCircle className="w-3.5 h-3.5" />,   severity: 'critical', label: 'Kirito Failed' },
  kirito_command_completed:{ icon: <Zap className="w-3.5 h-3.5" />,          severity: 'success',  label: 'Kirito Completed' },
  deerflow_cycle_started:  { icon: <Bot className="w-3.5 h-3.5" />,          severity: 'info',     label: 'DeerFlow Cycle Started' },
  deerflow_cycle_completed:{ icon: <Zap className="w-3.5 h-3.5" />,          severity: 'success',  label: 'DeerFlow Cycle Completed' },
  deerflow_paper_scan_started:{ icon: <FileText className="w-3.5 h-3.5" />,  severity: 'info',     label: 'DeerFlow Paper Scan' },
  deerflow_paper_scan_completed:{ icon: <Zap className="w-3.5 h-3.5" />,     severity: 'success',  label: 'DeerFlow Papers Ready' },
  deerflow_repo_scan_started:{ icon: <Globe className="w-3.5 h-3.5" />,      severity: 'info',     label: 'DeerFlow Repo Scan' },
  deerflow_repo_scan_completed:{ icon: <Zap className="w-3.5 h-3.5" />,      severity: 'success',  label: 'DeerFlow Repos Ready' },
  deerflow_daily_brief_started:{ icon: <FileText className="w-3.5 h-3.5" />, severity: 'info',     label: 'Daily Brief Running' },
  deerflow_daily_brief_completed:{ icon: <Zap className="w-3.5 h-3.5" />,    severity: 'success',  label: 'Daily Brief Ready' },
  payment_received:       { icon: <CreditCard className="w-3.5 h-3.5" />,    severity: 'success',  label: 'Payment Received' },
  invoice_sent:           { icon: <FileText className="w-3.5 h-3.5" />,      severity: 'info',     label: 'Invoice Sent' },
  pipeline_error:         { icon: <AlertCircle className="w-3.5 h-3.5" />,   severity: 'critical', label: 'Pipeline Error' },
  daemon_restart:         { icon: <Activity className="w-3.5 h-3.5" />,      severity: 'warning',  label: 'Daemon Restart' },
}

const SEVERITY_STYLES: Record<Severity, { border: string; glow: string; text: string; bg: string }> = {
  critical: { border: 'border-l-red',   glow: 'glow-critical', text: 'text-red',   bg: 'bg-red/8' },
  warning:  { border: 'border-l-amber', glow: 'glow-warning',  text: 'text-amber', bg: 'bg-amber/8' },
  info:     { border: 'border-l-gold',  glow: '',               text: 'text-gold',  bg: 'bg-gold/5' },
  success:  { border: 'border-l-green', glow: 'glow-success',  text: 'text-green', bg: 'bg-green/5' },
}

const SEVERITY_FILTERS: Severity[] = ['critical', 'warning', 'info', 'success']
const PRIORITY_PAYLOAD_KEYS = [
  'step',
  'spans',
  'risk',
  'executor',
  'target_agent',
  'task_type',
  'capability',
  'dispatch_mode',
  'task_id',
  'result_summary',
  'error',
  'reasoning',
]

function humanizeSignal(value: string) {
  return value.replace(/_/g, ' ')
}

function titleCaseSignal(value: string) {
  return humanizeSignal(value)
    .split(' ')
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatPayloadValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map(item => formatPayloadValue(item))
      .filter(Boolean)
      .join(', ')
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function getEventConfig(eventType: string) {
  const direct = EVENT_CONFIG[eventType]
  if (direct) {
    return direct
  }

  if (eventType.startsWith('kirito_command_')) {
    const lifecycle = eventType.replace('kirito_command_', '')
    const lifecycleLabels: Record<string, { icon: React.ReactNode; severity: Severity; label: string }> = {
      intake: { icon: <MessageSquare className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Intake' },
      received: { icon: <MessageSquare className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Intake' },
      classified: { icon: <Bot className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Routed' },
      routed: { icon: <Bot className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Routed' },
      dispatching: { icon: <ArrowRight className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Dispatching' },
      dispatched: { icon: <ArrowRight className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Dispatched' },
      queued: { icon: <Activity className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Queued' },
      running: { icon: <Activity className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Running' },
      local_action: { icon: <Globe className="w-3.5 h-3.5" />, severity: 'info', label: 'Kirito Local Action' },
      awaiting_followup: { icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning', label: 'Kirito Awaiting Follow-up' },
      awaiting_review: { icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning', label: 'Kirito Awaiting Review' },
      completed: { icon: <Zap className="w-3.5 h-3.5" />, severity: 'success', label: 'Kirito Completed' },
      failed: { icon: <AlertCircle className="w-3.5 h-3.5" />, severity: 'critical', label: 'Kirito Failed' },
      retry: { icon: <AlertTriangle className="w-3.5 h-3.5" />, severity: 'warning', label: 'Kirito Retry' },
    }

    if (lifecycleLabels[lifecycle]) {
      return lifecycleLabels[lifecycle]
    }
  }

  return {
    icon: <Activity className="w-3.5 h-3.5" />,
    severity: 'info' as Severity,
    label: titleCaseSignal(eventType),
  }
}

function summarizePayload(payload: Record<string, unknown>) {
  const entries = Object.entries(payload)
  const prioritized = PRIORITY_PAYLOAD_KEYS.flatMap((key) => {
    if (!(key in payload)) {
      return []
    }
    return [[key, payload[key]] as const]
  })
  const remaining = entries.filter(([key]) => !PRIORITY_PAYLOAD_KEYS.includes(key))

  return [...prioritized, ...remaining]
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${formatPayloadValue(value).slice(0, 30)}`)
    .join(' • ')
}

function getRelativeTime(dateString: string | null): string {
  if (!dateString) return ''
  const now = new Date()
  const date = new Date(dateString)
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

export function SignalLedger({ events }: SignalLedgerProps) {
  const [activeSeverity, setActiveSeverity] = useState<Set<Severity>>(new Set(SEVERITY_FILTERS))
  const [showFilters, setShowFilters] = useState(false)

  const toggleSeverity = (s: Severity) => {
    setActiveSeverity(prev => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  }

  const filteredEvents = useMemo(() => {
    return events.filter(e => {
      const config = getEventConfig(e.event_type)
      const severity = config?.severity ?? 'info'
      return activeSeverity.has(severity)
    })
  }, [events, activeSeverity])

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.5 }}
      className="glass-card-elevated hud-panel rounded-xl p-8"
    >
      {/* Header with filter toggle */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
          <Activity className="w-5 h-5 text-gold" />
          Signal Ledger
          <span className="text-xs text-muted-foreground">({filteredEvents.length})</span>
        </h2>
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`p-1.5 rounded-lg transition-colors ${showFilters ? 'bg-gold/10 text-gold' : 'text-muted-foreground hover:text-foreground'}`}
        >
          <Filter className="w-4 h-4" />
        </button>
      </div>

      {/* Filter bar */}
      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden mb-3"
          >
            <div className="flex gap-2 pb-3 border-b border-border">
              {SEVERITY_FILTERS.map(s => (
                <button
                  key={s}
                  onClick={() => toggleSeverity(s)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition-all ${
                    activeSeverity.has(s)
                      ? `${SEVERITY_STYLES[s].bg} ${SEVERITY_STYLES[s].text} border-current/30`
                      : 'bg-muted/30 text-muted-foreground border-border'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Event stream */}
      <ScrollArea className="h-[320px]">
        <div className="space-y-2 pr-2">
          {filteredEvents.length === 0 ? (
            <div className="text-center py-12">
              <img src="/assets/generated/states/no-events.svg" alt="" className="w-28 h-20 mx-auto mb-3 opacity-60" />
              <p className="text-xs text-muted-foreground">No events matching filters</p>
            </div>
          ) : (
            <AnimatedList delay={400} className="gap-2">
              {filteredEvents.map((event) => {
                const config = getEventConfig(event.event_type)
                const styles = SEVERITY_STYLES[config.severity]
                const isCritical = config.severity === 'critical' || config.severity === 'warning'

                return (
                  <div
                    key={event.id}
                    className={`p-3 rounded-lg border-l-2 ${styles.border} ${styles.bg} ${
                      isCritical ? styles.glow : ''
                    } transition-all`}
                  >
                    <div className="flex items-center gap-2">
                      <div className={styles.text}>{config.icon}</div>
                      <span className={`text-xs font-medium ${styles.text}`}>{config.label}</span>
                      <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
                        {getRelativeTime(event.created_at)}
                      </span>
                    </div>

                    {/* Payload summary */}
                    {event.payload && Object.keys(event.payload).length > 0 && (
                      <div className="mt-1.5 text-[11px] text-muted-foreground truncate">
                        {summarizePayload(event.payload)}
                      </div>
                    )}
                  </div>
                )
              })}
            </AnimatedList>
          )}
        </div>
      </ScrollArea>
    </motion.div>
  )
}
