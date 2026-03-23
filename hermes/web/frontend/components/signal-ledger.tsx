'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { Activity, Mail, Star, DollarSign, AlertTriangle, AlertCircle, MessageSquare, ArrowRight, RefreshCw, Bot } from 'lucide-react'

interface Event {
  id: string
  event_type: string
  payload: Record<string, unknown>
  created_at: string
  acknowledged: boolean
}

interface SignalLedgerProps {
  events: Event[]
}

const eventConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  email_sent: { icon: <Mail className="w-4 h-4" />, color: 'green', label: 'Email Sent' },
  emails_sent: { icon: <Mail className="w-4 h-4" />, color: 'green', label: 'Emails Sent' },
  lead_scored: { icon: <Star className="w-4 h-4" />, color: 'gold', label: 'Lead Scored' },
  demo_completed: { icon: <Activity className="w-4 h-4" />, color: 'teal', label: 'Demo Completed' },
  deal_closed: { icon: <DollarSign className="w-4 h-4" />, color: 'green', label: 'Deal Closed' },
  site_deployed: { icon: <Activity className="w-4 h-4" />, color: 'teal', label: 'Site Deployed' },
  review_needed: { icon: <AlertTriangle className="w-4 h-4" />, color: 'amber', label: 'Review Needed' },
  leads_discovered: { icon: <ArrowRight className="w-4 h-4" />, color: 'gold', label: 'Leads Discovered' },
  operator_message_sent: { icon: <MessageSquare className="w-4 h-4" />, color: 'gold', label: 'Operator Message' },
  agent_message_ack: { icon: <Bot className="w-4 h-4" />, color: 'teal', label: 'Agent Acknowledged' },
  payment_received: { icon: <DollarSign className="w-4 h-4" />, color: 'green', label: 'Payment Received' },
  approval_required: { icon: <AlertTriangle className="w-4 h-4" />, color: 'amber', label: 'Approval Required' },
  error: { icon: <AlertCircle className="w-4 h-4" />, color: 'red', label: 'Error' },
  reply_received: { icon: <MessageSquare className="w-4 h-4" />, color: 'teal', label: 'Reply Received' },
  pipeline_update: { icon: <ArrowRight className="w-4 h-4" />, color: 'gold', label: 'Pipeline Update' },
  agent_handoff: { icon: <Bot className="w-4 h-4" />, color: 'gold', label: 'Agent Handoff' },
  warning: { icon: <AlertTriangle className="w-4 h-4" />, color: 'amber', label: 'Warning' }
}

export function SignalLedger({ events }: SignalLedgerProps) {
  const formatTime = (dateString: string) => {
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)

    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  const getEventDetail = (event: Event): string => {
    const payload = event.payload
    switch (event.event_type) {
      case 'email_sent':
        return `Sent to ${payload.recipient}`
      case 'emails_sent':
        return `${payload.count ?? payload.emails_sent ?? '0'} sent`
      case 'lead_scored':
        return `${payload.business}: ${payload.previous} → ${payload.score}`
      case 'demo_completed':
        return `${payload.business} - ${payload.outcome}`
      case 'deal_closed':
        return `${payload.client ?? payload.business_name ?? 'Client'} · ${payload.amount ?? ''}`
      case 'site_deployed':
        return `${payload.business_name ?? payload.client ?? 'Deployment'} · ${payload.details ?? payload.url ?? ''}`
      case 'review_needed':
        return `${payload.reason ?? payload.type ?? 'Approval needed'} · ${payload.count ?? ''}`
      case 'leads_discovered':
        return `${payload.count ?? '0'} new leads`
      case 'operator_message_sent':
        return `${payload.target_agent ?? 'agent'} · ${payload.message ?? ''}`
      case 'agent_message_ack':
        return `${payload.agent ?? 'agent'} · ${payload.reply ?? payload.operator_message ?? ''}`
      case 'payment_received':
        return `${payload.amount} from ${payload.business}`
      case 'approval_required':
        return `${payload.action}: ${payload.business}`
      case 'error':
        return `${payload.message}`
      case 'reply_received':
        return `From ${payload.from} (${payload.sentiment})`
      case 'pipeline_update':
        return `${payload.stage}: ${payload.count} (${payload.change})`
      case 'agent_handoff':
        return `${payload.from} → ${payload.to}`
      case 'warning':
        return `${payload.message}`
      default:
        return JSON.stringify(payload)
    }
  }

  const getColorClasses = (color: string) => {
    switch (color) {
      case 'green': return { border: 'border-l-green', bg: 'bg-green/10', text: 'text-green' }
      case 'gold': return { border: 'border-l-gold', bg: 'bg-gold/10', text: 'text-gold' }
      case 'teal': return { border: 'border-l-teal', bg: 'bg-teal/10', text: 'text-teal' }
      case 'amber': return { border: 'border-l-amber', bg: 'bg-amber/10', text: 'text-amber' }
      case 'red': return { border: 'border-l-red', bg: 'bg-red/10', text: 'text-red', glow: 'shadow-red/20 shadow-lg' }
      default: return { border: 'border-l-muted-foreground', bg: 'bg-muted', text: 'text-muted-foreground' }
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.5 }}
      className="glass-card hud-panel rounded-xl p-5 md:p-6"
    >
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
          <Activity className="w-5 h-5 text-gold" />
          Signal Ledger
        </h2>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <RefreshCw className="w-3 h-3" />
          <span>Auto-refresh</span>
        </div>
      </div>

      <div className="space-y-2 max-h-[400px] overflow-y-auto pr-2">
        <AnimatePresence>
          {events.map((event, index) => {
            const config = eventConfig[event.event_type] || { 
              icon: <Activity className="w-4 h-4" />, 
              color: 'muted', 
              label: event.event_type 
            }
            const colors = getColorClasses(config.color)

            return (
              <motion.div
                key={event.id}
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 10 }}
                transition={{ delay: index * 0.05 }}
                className={`relative p-3 rounded-lg bg-muted/30 border-l-2 ${colors.border} ${config.color === 'red' ? colors.glow : ''} hover:bg-muted/50 transition-colors`}
              >
                <div className="flex items-start gap-3">
                  {/* Icon */}
                  <div className={`p-1.5 rounded-md ${colors.bg} ${colors.text}`}>
                    {config.icon}
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2 mb-0.5">
                      <span className={`text-sm font-medium ${colors.text}`}>
                        {config.label}
                      </span>
                      <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                        {formatTime(event.created_at)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground truncate">
                      {getEventDetail(event)}
                    </p>
                  </div>

                  {/* Unacknowledged indicator */}
                  {!event.acknowledged && (
                    <div className="w-2 h-2 rounded-full bg-amber animate-pulse" />
                  )}
                </div>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}
