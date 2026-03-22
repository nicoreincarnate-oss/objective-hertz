'use client'

import { motion } from 'framer-motion'
import { DollarSign, Clock, Mail, Percent } from 'lucide-react'
import { CountUp } from './count-up'

interface MetricsRowProps {
  revenueCleared: number
  pendingRevenue: number
  emailsToday: number
  emailsWeek: number
  closeRate: number
}

export function MetricsRow({ revenueCleared, pendingRevenue, emailsToday, emailsWeek, closeRate }: MetricsRowProps) {
  const metrics = [
    {
      icon: <DollarSign className="w-5 h-5" />,
      label: 'Revenue Cleared',
      value: revenueCleared,
      prefix: '$',
      decimals: 2,
      suffix: '',
      color: 'green' as const,
      subtext: 'This month'
    },
    {
      icon: <Clock className="w-5 h-5" />,
      label: 'Pending Revenue',
      value: pendingRevenue,
      prefix: '$',
      decimals: 2,
      suffix: '',
      color: pendingRevenue > 0 ? 'amber' as const : 'muted' as const,
      subtext: 'Awaiting payment'
    },
    {
      icon: <Mail className="w-5 h-5" />,
      label: 'Emails Today',
      value: emailsToday,
      prefix: '',
      decimals: 0,
      suffix: '',
      color: 'gold' as const,
      subtext: `${emailsWeek} this week`
    },
    {
      icon: <Percent className="w-5 h-5" />,
      label: 'Close Rate',
      value: closeRate,
      prefix: '',
      decimals: 1,
      suffix: '%',
      color: closeRate >= 5 ? 'green' as const : 'amber' as const,
      subtext: 'Leads → Closed'
    }
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
      {metrics.map((metric, index) => (
        <MetricCard key={metric.label} metric={metric} index={index} />
      ))}
    </div>
  )
}

function MetricCard({ metric, index }: { metric: {
  icon: React.ReactNode
  label: string
  value: number
  prefix: string
  decimals: number
  suffix: string
  color: 'green' | 'amber' | 'gold' | 'muted'
  subtext: string
}, index: number }) {
  const colorClasses = {
    green: 'text-green',
    amber: 'text-amber',
    gold: 'text-gold',
    muted: 'text-muted-foreground'
  }

  const glowClasses = {
    green: 'group-hover:shadow-green/20',
    amber: 'group-hover:shadow-amber/20',
    gold: 'group-hover:shadow-gold/20',
    muted: 'group-hover:shadow-muted/10'
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      whileHover={{ y: -3 }}
      className={`group relative glass-card hud-panel rounded-xl p-4 md:p-5 transition-shadow duration-300 hover:shadow-xl ${glowClasses[metric.color]}`}
    >
      {/* Shimmer overlay */}
      <div className="absolute inset-0 rounded-xl overflow-hidden pointer-events-none">
        <div className="shimmer absolute inset-0" />
      </div>

      <div className="relative">
        {/* Icon and Label */}
        <div className="flex items-center gap-2 mb-3">
          <div className={`${colorClasses[metric.color]} opacity-70`}>
            {metric.icon}
          </div>
          <span className="text-xs text-muted-foreground uppercase tracking-wider">
            {metric.label}
          </span>
        </div>

        {/* Value */}
        <div className={`text-2xl md:text-3xl font-bold ${colorClasses[metric.color]} mb-1`}>
          <CountUp 
            end={metric.value} 
            prefix={metric.prefix}
            suffix={metric.suffix}
            decimals={metric.decimals}
          />
        </div>

        {/* Subtext */}
        <p className="text-xs text-muted-foreground">
          {metric.subtext}
        </p>
      </div>
    </motion.div>
  )
}
