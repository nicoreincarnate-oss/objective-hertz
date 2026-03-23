'use client'

import { motion } from 'framer-motion'
import { TrendingUp } from 'lucide-react'

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

interface PipelinePulseProps {
  data: PipelineData
}

const stages = [
  { key: 'discovered', label: 'Discovered' },
  { key: 'researched', label: 'Researched' },
  { key: 'email_sent', label: 'Email Sent' },
  { key: 'followed_up', label: 'Followed Up' },
  { key: 'replied', label: 'Replied' },
  { key: 'interested', label: 'Interested' },
  { key: 'demo_built', label: 'Demo Built' },
  { key: 'proposal_sent', label: 'Proposal Sent' },
  { key: 'closed', label: 'Closed' },
  { key: 'building', label: 'Building' },
  { key: 'deployed', label: 'Deployed' },
  { key: 'invoiced', label: 'Invoiced' },
  { key: 'paid', label: 'Paid' }
] as const

export function PipelinePulse({ data }: PipelinePulseProps) {
  const values = Object.values(data).filter((v): v is number => typeof v === 'number')
  const maxValue = values.length > 0 ? Math.max(...values) : 1

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.4 }}
      className="glass-card hud-panel rounded-xl p-5 md:p-6"
    >
      <h2 className="text-lg font-semibold text-foreground mb-5 flex items-center gap-2">
        <TrendingUp className="w-5 h-5 text-gold" />
        Pipeline Pulse
      </h2>

      <div className="space-y-3">
        {stages.map((stage, index) => {
          const value = data[stage.key] ?? 0
          const percentage = maxValue > 0 ? (value / maxValue) * 100 : 0

          return (
            <div key={stage.key} className="group">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
                  {stage.label}
                </span>
                <span className="text-sm font-semibold text-foreground tabular-nums">
                  {value.toLocaleString()}
                </span>
              </div>
              <div className="relative h-2.5 bg-muted/50 rounded-full overflow-hidden">
                {/* Glow effect */}
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${percentage}%` }}
                  transition={{ duration: 1, delay: 0.1 * index, ease: "easeOut" }}
                  className="absolute inset-y-0 left-0 blur-sm opacity-50"
                  style={{
                    background: `linear-gradient(90deg, #14b8a6, #d1a968)`
                  }}
                />
                {/* Main bar */}
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${percentage}%` }}
                  transition={{ duration: 1, delay: 0.1 * index, ease: "easeOut" }}
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    background: `linear-gradient(90deg, #14b8a6, #d1a968)`
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>

      {/* Summary */}
      <div className="mt-6 pt-4 border-t border-border flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Conversion Rate</span>
        <span className="text-sm font-semibold text-gold">
          {((data.paid / data.discovered) * 100).toFixed(2)}%
        </span>
      </div>
    </motion.div>
  )
}
