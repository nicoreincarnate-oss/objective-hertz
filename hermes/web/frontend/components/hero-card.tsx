'use client'

import { motion } from 'framer-motion'
import { Activity, Mail, Users, TrendingUp, Clock } from 'lucide-react'

interface HeroCardProps {
  mode: 'review' | 'autonomous'
  pendingApprovals: number
  emailsSent: number
  warmLeads: number
  salesClosed: number
  lastSync: string
}

export function HeroCard({ mode, pendingApprovals, emailsSent, warmLeads, salesClosed, lastSync }: HeroCardProps) {
  const isReviewMode = mode === 'review'
  
  const getHeadline = () => {
    if (pendingApprovals > 0) {
      return `${pendingApprovals} approval${pendingApprovals > 1 ? 's' : ''} waiting`
    }
    return 'Runtime is clear'
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6 }}
      className="relative overflow-hidden"
    >
      {/* Floating orbs background */}
      <div className="absolute inset-0 overflow-hidden rounded-2xl">
        <motion.div
          animate={{
            x: [0, 30, 0],
            y: [0, -20, 0],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
          className="absolute -top-20 -left-20 w-60 h-60 rounded-full bg-gold/10 blur-3xl"
        />
        <motion.div
          animate={{
            x: [0, -20, 0],
            y: [0, 30, 0],
          }}
          transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
          className="absolute -bottom-20 -right-20 w-80 h-80 rounded-full bg-teal/8 blur-3xl"
        />
        <motion.div
          animate={{
            x: [0, 15, 0],
            y: [0, 15, 0],
          }}
          transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
          className="absolute top-1/2 left-1/3 w-40 h-40 rounded-full bg-gold/5 blur-2xl"
        />
      </div>

      {/* Main card */}
      <div className="relative glass-card hud-panel rounded-2xl p-6 breathing-glow">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="absolute inset-0 bg-gold/30 blur-md rounded-lg" />
              <div className="relative bg-gradient-to-br from-gold to-gold-dim px-3 py-1 rounded-lg">
                <span className="text-background font-bold text-sm tracking-[0.2em]">PERSEUS</span>
              </div>
            </div>
          </div>
          
          {/* Mode pill */}
          <motion.div
            animate={{ opacity: [0.8, 1, 0.8] }}
            transition={{ duration: 2, repeat: Infinity }}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${
              isReviewMode 
                ? 'bg-amber/10 border-amber/30 text-amber' 
                : 'bg-green/10 border-green/30 text-green'
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${isReviewMode ? 'bg-amber' : 'bg-green'}`} />
            <span className="text-xs font-semibold tracking-wide">
              {isReviewMode ? 'Review Mode' : 'Autonomous'}
            </span>
          </motion.div>
        </div>

        {/* Headline */}
        <div className="mb-6">
          <h1 className="text-3xl md:text-4xl font-bold text-foreground mb-2 text-balance">
            {getHeadline()}
          </h1>
          <p className="text-muted-foreground text-sm md:text-base">
            {emailsSent} emails sent today • {warmLeads} warm leads • {salesClosed} sales closed
          </p>
        </div>

        {/* Signal rail */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SignalPill 
            icon={<Activity className="w-3.5 h-3.5" />}
            label="Mode"
            value={isReviewMode ? 'Review' : 'Auto'}
            color={isReviewMode ? 'amber' : 'green'}
          />
          <SignalPill 
            icon={<Mail className="w-3.5 h-3.5" />}
            label="Review"
            value={pendingApprovals.toString()}
            color={pendingApprovals > 0 ? 'amber' : 'green'}
          />
          <SignalPill 
            icon={<TrendingUp className="w-3.5 h-3.5" />}
            label="Closed"
            value={salesClosed.toString()}
            color="green"
          />
          <SignalPill 
            icon={<Clock className="w-3.5 h-3.5" />}
            label="Sync"
            value={lastSync}
            color="muted"
          />
        </div>
      </div>
    </motion.div>
  )
}

function SignalPill({ 
  icon, 
  label, 
  value, 
  color 
}: { 
  icon: React.ReactNode
  label: string
  value: string
  color: 'amber' | 'green' | 'muted'
}) {
  const colorClasses = {
    amber: 'bg-amber/10 border-amber/20 text-amber',
    green: 'bg-green/10 border-green/20 text-green',
    muted: 'bg-muted border-border text-muted-foreground'
  }

  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border ${colorClasses[color]}`}>
      {icon}
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wider opacity-70">{label}</span>
        <span className="text-xs font-semibold">{value}</span>
      </div>
    </div>
  )
}
