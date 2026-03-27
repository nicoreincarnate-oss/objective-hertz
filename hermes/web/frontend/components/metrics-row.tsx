'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { DollarSign, Clock, Mail, Percent, Zap, Wallet } from 'lucide-react'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { NumberTicker } from '@/components/ui/number-ticker'
import { HyperText } from '@/components/ui/hyper-text'

interface MetricsRowProps {
  revenueCleared: number
  pendingRevenue: number
  emailsToday: number
  emailsWeek: number
  closeRate: number
  pipelineVelocity?: number
  budgetRemaining?: number
  budgetTotal?: number
  metricHistory?: Record<string, number[]>
}

type MetricColor = 'green' | 'amber' | 'gold' | 'muted' | 'teal'

interface MetricDef {
  icon: React.ReactNode
  label: string
  value: number
  prefix: string
  decimals: number
  suffix: string
  color: MetricColor
  subtext: string
  sparklineColor: string
  // Real time-series history — provided by WebSocket/backend when available
  history?: number[]
}

// Sparkline data comes from the history prop when available.
// Returns null when no real history exists — components show "No data yet" instead of fake charts.

export function MetricsRow({
  revenueCleared,
  pendingRevenue,
  emailsToday,
  emailsWeek,
  closeRate,
  pipelineVelocity = 0,
  budgetRemaining,
  budgetTotal = 800,
  metricHistory = {},
}: MetricsRowProps) {
  const budget = budgetRemaining ?? budgetTotal

  const metrics: MetricDef[] = [
    {
      icon: <DollarSign className="w-4 h-4" />,
      label: 'Revenue',
      value: revenueCleared,
      prefix: '$',
      decimals: 0,
      suffix: '',
      color: 'green',
      subtext: 'Cleared',
      sparklineColor: '#62f1b5',
      history: metricHistory['revenue_cleared'],
    },
    {
      icon: <Clock className="w-4 h-4" />,
      label: 'Pending',
      value: pendingRevenue,
      prefix: '$',
      decimals: 0,
      suffix: '',
      color: pendingRevenue > 0 ? 'amber' : 'muted',
      subtext: 'Awaiting',
      sparklineColor: '#ffb347',
      history: metricHistory['revenue_pending'],
    },
    {
      icon: <Mail className="w-4 h-4" />,
      label: 'Emails',
      value: emailsToday,
      prefix: '',
      decimals: 0,
      suffix: '',
      color: 'gold',
      subtext: `${emailsWeek}/wk`,
      sparklineColor: '#58e0ff',
      history: metricHistory['emails_sent_today'],
    },
    {
      icon: <Percent className="w-4 h-4" />,
      label: 'Close %',
      value: closeRate,
      prefix: '',
      decimals: 1,
      suffix: '%',
      color: closeRate >= 5 ? 'green' : 'amber',
      subtext: 'Conversion',
      sparklineColor: closeRate >= 5 ? '#62f1b5' : '#ffb347',
      history: metricHistory['sales_closed'],
    },
    {
      icon: <Zap className="w-4 h-4" />,
      label: 'Velocity',
      value: pipelineVelocity,
      prefix: '',
      decimals: 1,
      suffix: '/d',
      color: 'teal',
      subtext: 'Leads/day',
      sparklineColor: '#39f3e2',
    },
    {
      icon: <Wallet className="w-4 h-4" />,
      label: 'Budget',
      value: budget,
      prefix: '$',
      decimals: 0,
      suffix: '',
      color: budget < 200 ? 'amber' : 'green',
      subtext: `of $${budgetTotal}`,
      sparklineColor: budget < 200 ? '#ffb347' : '#62f1b5',
    },
  ]

  return (
    <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
      {metrics.map((metric, index) => (
        <FlipMetricCard key={metric.label} metric={metric} index={index} />
      ))}
    </div>
  )
}

function FlipMetricCard({ metric, index }: { metric: MetricDef; index: number }) {
  const [flipped, setFlipped] = useState(false)

  const colorClasses: Record<MetricColor, string> = {
    green: 'text-green',
    amber: 'text-amber',
    gold: 'text-gold',
    teal: 'text-teal',
    muted: 'text-muted-foreground',
  }

  const hasHistory = metric.history && metric.history.length >= 2
  const sparkData = hasHistory ? metric.history!.map(v => ({ v })) : null

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.06 }}
      onClick={() => setFlipped(!flipped)}
      className="cursor-pointer select-none"
      // Fixed height container — both faces absolutely positioned inside
      style={{ height: 120, perspective: 1000 }}
    >
      <div
        style={{
          position: 'relative',
          width: '100%',
          height: '100%',
          transformStyle: 'preserve-3d',
          transition: 'transform 0.5s ease',
          transform: flipped ? 'rotateY(180deg)' : 'none',
        }}
      >
        {/* ─── FRONT ─── */}
        <div
          className="glass-card hud-panel rounded-xl p-3"
          style={{
            position: 'absolute',
            inset: 0,
            backfaceVisibility: 'hidden',
          }}
        >
          <div className="flex items-center gap-1.5 mb-2">
            <div className={`${colorClasses[metric.color]} opacity-60`}>{metric.icon}</div>
            <HyperText text={metric.label} className="text-[10px] text-muted-foreground uppercase tracking-wider truncate" />
          </div>
          <div className={`text-xl font-bold ${colorClasses[metric.color]} leading-none mb-1`}>
            {metric.prefix}
            <NumberTicker
              value={metric.value}
              decimalPlaces={metric.decimals}
              className={colorClasses[metric.color]}
            />
            {metric.suffix}
          </div>
          <span className="text-[10px] text-muted-foreground">{metric.subtext}</span>
        </div>

        {/* ─── BACK ─── */}
        <div
          className="glass-card hud-panel rounded-xl p-3 flex flex-col"
          style={{
            position: 'absolute',
            inset: 0,
            backfaceVisibility: 'hidden',
            transform: 'rotateY(180deg)',
          }}
        >
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
            {metric.label} — trend
          </span>
          {sparkData ? (
            <>
              <div className="flex-1 min-h-0" style={{ height: 52 }}>
                <ResponsiveContainer width="100%" height={52}>
                  <AreaChart data={sparkData}>
                    <defs>
                      <linearGradient id={`sp-${index}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={metric.sparklineColor} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={metric.sparklineColor} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <Area
                      type="monotone"
                      dataKey="v"
                      stroke={metric.sparklineColor}
                      strokeWidth={2}
                      fill={`url(#sp-${index})`}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className={`text-sm font-bold ${colorClasses[metric.color]}`}>
                {metric.prefix}{metric.value.toFixed(metric.decimals)}{metric.suffix}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <span className="text-[11px] text-muted-foreground/50">No trend data yet</span>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}
