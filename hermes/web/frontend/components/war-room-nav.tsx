'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { motion } from 'framer-motion'
import {
  LayoutDashboard, GitBranch, Bot, Brain, LogOut,
} from 'lucide-react'
import { Marquee } from '@/components/ui/marquee'
import { useWarRoom } from '@/contexts/war-room-context'

const NAV_ITEMS = [
  { href: '/', label: 'Command', icon: LayoutDashboard, shortcut: '⌘1' },
  { href: '/pipeline', label: 'Pipeline', icon: GitBranch, shortcut: '⌘2' },
  { href: '/agents', label: 'Agents', icon: Bot, shortcut: '⌘3' },
  { href: '/intel', label: 'Intel', icon: Brain, shortcut: '⌘4' },
]

function AlertTicker() {
  const { events } = useWarRoom()
  const criticalEvents = events.filter(e =>
    ['approval_required', 'pipeline_error', 'review_needed', 'daemon_restart'].includes(e.event_type)
  )

  if (criticalEvents.length === 0) return null

  return (
    <div className="border-b border-border/50 bg-red/5">
      <Marquee className="py-1" pauseOnHover>
        {criticalEvents.slice(0, 8).map((event) => (
          <span key={event.id} className="mx-6 text-[11px] text-red/80 font-medium">
            ⚠ {event.event_type.replace(/_/g, ' ').toUpperCase()}
            {event.payload && typeof event.payload === 'object' && 'lead' in event.payload
              ? ` — ${event.payload.lead}`
              : ''}
          </span>
        ))}
      </Marquee>
    </div>
  )
}

export function WarRoomNav() {
  const pathname = usePathname()
  const { connectionStatus, healthStatus } = useWarRoom()

  return (
    <nav className="sticky top-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur-xl">
      {/* Alert ticker */}
      <AlertTicker />

      {/* Nav bar */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-12">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="relative">
              <div
                className={`w-2 h-2 rounded-full ${
                  healthStatus === 'green' ? 'bg-green' :
                  healthStatus === 'amber' ? 'bg-amber' : 'bg-red'
                }`}
              />
            </div>
            <span className="text-sm font-bold tracking-[0.15em] text-gold">PERSEUS</span>
          </div>

          {/* Nav links */}
          <div className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href
              const Icon = item.icon
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    isActive
                      ? 'text-gold'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {isActive && (
                    <motion.div
                      layoutId="nav-active"
                      className="absolute inset-0 bg-gold/8 border border-gold/20 rounded-lg"
                      transition={{ type: 'spring', duration: 0.4, bounce: 0.15 }}
                    />
                  )}
                  <Icon className="w-3.5 h-3.5 relative z-10" />
                  <span className="relative z-10 hidden sm:inline">{item.label}</span>
                </Link>
              )
            })}
          </div>

          {/* Status */}
          <div className="flex items-center gap-2">
            <span className={`w-1.5 h-1.5 rounded-full ${
              connectionStatus === 'connected' ? 'bg-green' :
              connectionStatus === 'polling' ? 'bg-amber' : 'bg-red/50'
            }`} />
            <span className="text-[10px] text-muted-foreground hidden sm:inline">
              {connectionStatus === 'connected' ? 'Live' : connectionStatus === 'polling' ? 'Polling' : 'Offline'}
            </span>
          </div>
        </div>
      </div>
    </nav>
  )
}
