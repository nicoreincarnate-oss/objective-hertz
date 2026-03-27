'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { DollarSign, Clock, Mail, Percent, Zap, Wallet } from 'lucide-react'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { NumberTicker } from '@/components/ui/number-ticker'

interface MetricsRowProps {
  revenueCleared: number
  pendingRevenue: number
  emailsToday: number
  emailsWeek: number
  closeRate: number
  pipelineVelocity?: number
  budgetRemaining?: number
  budgetTotal?: number
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
  shineBorderColor: string | string[]
}

// Generate fake sparkline data for demo — in production this comes from use-sparkline-data hook
function generateSparkline(value: number, trend: 'up' | 'down' | 'flat' = 'up'): Array<{ v: number }> {
  const points = 20
  const data: Array<{ v: number }> = []
  let base = value * 0.6
  for (let i = 0; i < points; i++) {
    const noise = (Math.random() - 0.5) * value * 0.15
    if (trend === 'up') base += value * 0.02
    else if (trend === 'down') base -= value * 0.01
    data.push({ v: Math.max(0, base + noise) })
  }
  return data
}

export function MetricsRow({
  revenueCleared,
  pendingRevenue,
  emailsToday,
  emailsWeek,
  closeRate,
  pipelineVelocity = 0,
  budgetRemaining,
  budgetTotal = 800,
}: MetricsRowProps) {
  const budget = budgetRemaining ?? budgetTotal

  const metrics: MetricDef[] = [
    {
      icon: <DollarSign className="w-5 h-5" />,
      label: 'Revenue Cleared',
      value: revenueCleared,
      prefix: '$',
      decimals: 2,
      suffix: '',
      color: 'green',
      subtext: 'This month',
      sparklineColor: '#62f1b5',
      shineBorderColor: ['#62f1b5', '#39f3e2'],
    },
    {
      icon: <Clock className="w-5 h-5" />,
      label: 'Pending Revenue',
      value: pendingRevenue,
      prefix: '$',
      decimals: 2,
      suffix: '',
      color: pendingRevenue > 0 ? 'amber' : 'muted',
      subtext: 'Awaiting payment',
      sparklineColor: '#ffb347',
      shineBorderColor: pendingRevenue > 0 ? ['#ffb347', '#ff8c42'] : ['#444', '#555'],
    },
    {
      icon: <Mail className="w-5 h-5" />,
      label: 'Emails Today',
      value: emailsToday,
      prefix: '',
      decimals: 0,
      suffix: '',
      color: 'gold',
      subtext: `${emailsWeek} this week`,
      sparklineColor: '#58e0ff',
      shineBorderColor: ['#58e0ff', '#ffd700'],
    },
    {
      icon: <Percent className="w-5 h-5" />,
      label: 'Close Rate',
      value: closeRate,
      prefix: '',
      decimals: 1,
      suffix: '%',
      color: closeRate >= 5 ? 'green' : 'amber',
      subtext: 'Leads → Closed',
      sparklineColor: closeRate >= 5 ? '#62f1b5' : '#ffb347',
      shineBorderColor: closeRate >= 5 ? ['#62f1b5', '#39f3e2'] : ['#ffb347', '#ff8c42'],
    },
    {
      icon: <Zap className="w-5 h-5" />,
      label: 'Pipeline Velocity',
      value: pipelineVelocity,
      prefix: '',
      decimals: 1,
      suffix: '/day',
      color: 'teal',
      subtext: 'Leads advancing',
      sparklineColor: '#39f3e2',
      shineBorderColor: ['#39f3e2', '#58e0ff'],
    },
    {
      icon: <Wallet className="w-5 h-5" />,
      label: 'Budget Remaining',
      value: budget,
      prefix: '$',
      decimals: 0,
      suffix: '',
      color: budget < 200 ? 'amber' : 'green',
      subtext: `of $${budgetTotal}/mo`,
      sparklineColor: budget < 200 ? '#ffb347' : '#62f1b5',
      shineBorderColor: budget < 200 ? ['#ffb347', '#ff8c42'] : ['#62f1b5', '#39f3e2'],
    },
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3 md:gap-4">
      {metrics.map((metric, index) => (
        <FlipMetricCard key={metric.label} metric={metric} index={index} />
      ))}
    </div>
  )
}

function FlipMetricCard({ metric, index }: { metric: MetricDef; index: number }) {
  const [isFlipped, setIsFlipped] = useState(false)

  const colorClasses: Record<MetricColor, string> = {
    green: 'text-green',
    amber: 'text-amber',
    gold: 'text-gold',
    teal: 'text-teal',
    muted: 'text-muted-foreground',
  }

  const glowClasses: Record<MetricColor, string> = {
    green: 'group-hover:shadow-green/20',
    amber: 'group-hover:shadow-amber/20',
    gold: 'group-hover:shadow-gold/20',
    teal: 'group-hover:shadow-teal/20',
    muted: 'group-hover:shadow-muted/10',
  }

  const sparkData = generateSparkline(metric.value || 1)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: index * 0.08 }}
      className={`cursor-pointer h-[140px] ${isFlipped ? 'flipped' : ''}`}
      style={{ perspective: '1200px', zIndex: isFlipped ? 10 : 1 }}
      onClick={() => setIsFlipped(!isFlipped)}
    >
      <div
        className="relative w-full h-full"
        style={{
          transformStyle: 'preserve-3d',
          transition: 'transform 0.6s cubic-bezier(0.4, 0, 0.2, 1)',
          transform: isFlipped ? 'rotateY(180deg)' : 'rotateY(0deg)',
        }}
      >
        {/* Front face */}
        <div
          className={`absolute inset-0 glass-card hud-panel rounded-xl p-4 md:p-5 group transition-shadow duration-300 hover:shadow-xl ${glowClasses[metric.color]}`}
          style={{ backfaceVisibility: 'hidden' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <div className={`${colorClasses[metric.color]} opacity-70`}>
              {metric.icon}
            </div>
            <span className="text-xs text-muted-foreground uppercase tracking-wider">
              {metric.label}
            </span>
          </div>

          <div className={`text-2xl md:text-3xl font-bold ${colorClasses[metric.color]} mb-1`}>
            {metric.prefix}
            <NumberTicker
              value={metric.value}
              decimalPlaces={metric.decimals}
              className={colorClasses[metric.color]}
            />
            {metric.suffix}
          </div>

          <p className="text-xs text-muted-foreground">{metric.subtext}</p>

          <div className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-40 transition-opacity">
            <span className="text-[9px] text-muted-foreground">click for chart</span>
          </div>
        </div>

        {/* Back face — sparkline chart */}
        <div
          className={`absolute inset-0 glass-card hud-panel rounded-xl p-4 ${glowClasses[metric.color]}`}
          style={{
            backfaceVisibility: 'hidden',
            transform: 'rotateY(180deg)',
          }}
        >
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
              {metric.label} — Trend
            </span>
          </div>

          <div style={{ height: 70 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={sparkData}>
                <defs>
                  <linearGradient id={`gradient-${index}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={metric.sparklineColor} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={metric.sparklineColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke={metric.sparklineColor}
                  strokeWidth={2}
                  fill={`url(#gradient-${index})`}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className={`text-sm font-bold ${colorClasses[metric.color]} mt-1`}>
            {metric.prefix}{metric.value.toFixed(metric.decimals)}{metric.suffix}
          </div>
        </div>
      </div>
    </motion.div>
  )
}
