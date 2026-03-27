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
      const config = EVENT_CONFIG[e.event_type]
      const severity = config?.severity ?? 'info'
      return activeSeverity.has(severity)
    })
  }, [events, activeSeverity])

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.5 }}
      className="glass-card-elevated hud-panel rounded-xl p-5 md:p-6"
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
          <AnimatePresence mode="popLayout" initial={false}>
            {filteredEvents.map((event) => {
              const config = EVENT_CONFIG[event.event_type] ?? {
                icon: <Activity className="w-3.5 h-3.5" />,
                severity: 'info' as Severity,
                label: event.event_type.replace(/_/g, ' '),
              }
              const styles = SEVERITY_STYLES[config.severity]
              const isCritical = config.severity === 'critical' || config.severity === 'warning'

              return (
                <motion.div
                  key={event.id}
                  initial={{ opacity: 0, x: 50 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -50 }}
                  transition={{ duration: 0.25 }}
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
                      {Object.entries(event.payload)
                        .slice(0, 3)
                        .map(([k, v]) => `${k}: ${String(v).slice(0, 30)}`)
                        .join(' • ')}
                    </div>
                  )}
                </motion.div>
              )
            })}
          </AnimatePresence>

          {filteredEvents.length === 0 && (
            <div className="text-center py-12">
              <img src="/assets/generated/states/no-events.svg" alt="" className="w-28 h-20 mx-auto mb-3 opacity-60" />
              <p className="text-xs text-muted-foreground">No events matching filters</p>
            </div>
          )}
        </div>
      </ScrollArea>
    </motion.div>
  )
}
