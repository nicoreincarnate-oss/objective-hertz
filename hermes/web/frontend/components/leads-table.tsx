'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Users, ChevronDown, ChevronUp, ArrowUpDown } from 'lucide-react'

interface Lead {
  id: string
  business_name: string
  email: string
  industry: string
  status: string
  lead_score: number
  created_at: string
}

interface LeadsTableProps {
  leads: Lead[]
}

const statusConfig: Record<string, { label: string; color: string }> = {
  researching: { label: 'Researching', color: 'text-muted-foreground bg-muted' },
  contacted: { label: 'Contacted', color: 'text-gold bg-gold/10' },
  replied: { label: 'Replied', color: 'text-teal bg-teal/10' },
  interested: { label: 'Interested', color: 'text-amber bg-amber/10' },
  demo_scheduled: { label: 'Demo Set', color: 'text-teal bg-teal/10' },
  proposal_sent: { label: 'Proposal', color: 'text-gold bg-gold/10' },
  closed_won: { label: 'Closed', color: 'text-green bg-green/10' },
  closed_lost: { label: 'Lost', color: 'text-red bg-red/10' }
}

export function LeadsTable({ leads }: LeadsTableProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [sortBy, setSortBy] = useState<'score' | 'date'>('score')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')

  const sortedLeads = [...leads].sort((a, b) => {
    if (sortBy === 'score') {
      return sortOrder === 'desc' ? b.lead_score - a.lead_score : a.lead_score - b.lead_score
    }
    const dateA = new Date(a.created_at).getTime()
    const dateB = new Date(b.created_at).getTime()
    return sortOrder === 'desc' ? dateB - dateA : dateA - dateB
  })

  const displayedLeads = isExpanded ? sortedLeads : sortedLeads.slice(0, 5)

  const toggleSort = (field: 'score' | 'date') => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  const getScoreColor = (score: number) => {
    if (score >= 90) return 'text-green'
    if (score >= 75) return 'text-gold'
    if (score >= 50) return 'text-amber'
    return 'text-muted-foreground'
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.6 }}
      className="glass-card hud-panel rounded-xl overflow-hidden"
    >
      {/* Header */}
      <div className="p-5 md:p-6 border-b border-border">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Users className="w-5 h-5 text-gold" />
            Recent Leads
          </h2>
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {isExpanded ? (
              <>
                <span>Show less</span>
                <ChevronUp className="w-4 h-4" />
              </>
            ) : (
              <>
                <span>Show all ({leads.length})</span>
                <ChevronDown className="w-4 h-4" />
              </>
            )}
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Business
              </th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider hidden md:table-cell">
                Industry
              </th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Status
              </th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                <button
                  onClick={() => toggleSort('score')}
                  className="flex items-center gap-1 hover:text-foreground transition-colors"
                >
                  Score
                  <ArrowUpDown className="w-3 h-3" />
                </button>
              </th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider hidden sm:table-cell">
                <button
                  onClick={() => toggleSort('date')}
                  className="flex items-center gap-1 hover:text-foreground transition-colors"
                >
                  Date
                  <ArrowUpDown className="w-3 h-3" />
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {displayedLeads.map((lead, index) => {
                const status = statusConfig[lead.status] || { label: lead.status, color: 'text-muted-foreground bg-muted' }
                
                return (
                  <motion.tr
                    key={lead.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ delay: index * 0.05 }}
                    className="border-b border-border/50 hover:bg-muted/30 transition-colors"
                  >
                    <td className="px-5 py-4">
                      <div>
                        <div className="text-sm font-medium text-foreground">
                          {lead.business_name}
                        </div>
                        <div className="text-xs text-muted-foreground truncate max-w-[150px]">
                          {lead.email}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-4 hidden md:table-cell">
                      <span className="text-sm text-muted-foreground">
                        {lead.industry}
                      </span>
                    </td>
                    <td className="px-5 py-4">
                      <span className={`inline-flex px-2 py-1 rounded-full text-xs font-medium ${status.color}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-5 py-4">
                      <span className={`text-sm font-semibold tabular-nums ${getScoreColor(lead.lead_score)}`}>
                        {lead.lead_score}
                      </span>
                    </td>
                    <td className="px-5 py-4 hidden sm:table-cell">
                      <span className="text-xs text-muted-foreground">
                        {formatDate(lead.created_at)}
                      </span>
                    </td>
                  </motion.tr>
                )
              })}
            </AnimatePresence>
          </tbody>
        </table>
      </div>
    </motion.div>
  )
}
