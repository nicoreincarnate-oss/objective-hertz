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
    <nav className="sticky top-0 z-30 border-b border-border/50 bg-background/80 backdrop-blur-xl">
      {/* Alert ticker */}
      <AlertTicker />

      {/* Nav bar */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14">
          {/* Logo — slightly larger */}
          <div className="flex items-center gap-2.5">
            <div className="relative">
              <div
                className={`w-2.5 h-2.5 rounded-full ${
                  healthStatus === 'green' ? 'bg-green shadow-[0_0_6px_currentColor] text-green-400' :
                  healthStatus === 'amber' ? 'bg-amber shadow-[0_0_6px_currentColor] text-amber-400' : 'bg-red shadow-[0_0_6px_currentColor] text-red-400'
                }`}
              />
            </div>
            <span className="text-base font-bold tracking-[0.18em] text-gold">PERSEUS</span>
          </div>

          {/* Nav links */}
          <div className="flex items-center gap-0.5">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href
              const Icon = item.icon
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`relative flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium cursor-pointer transition-all duration-200 group ${
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
                  {/* Bottom glow line on active item */}
                  {isActive && (
                    <motion.div
                      layoutId="nav-glow"
                      className="absolute bottom-0 left-1/2 -translate-x-1/2 w-6 h-[2px] rounded-full bg-gold/60"
                      style={{ boxShadow: '0 0 6px 1px rgba(88,224,255,0.5)' }}
                      transition={{ type: 'spring', duration: 0.4, bounce: 0.15 }}
                    />
                  )}
                  <Icon className="w-3.5 h-3.5 relative z-10" />
                  <span className="relative z-10 hidden sm:inline">{item.label}</span>
                  {/* Keyboard shortcut hint */}
                  <span className="relative z-10 hidden lg:inline text-[9px] text-muted-foreground/40 font-mono ml-0.5">
                    {item.shortcut}
                  </span>
                </Link>
              )
            })}
          </div>

          {/* Connection status */}
          <div className="flex items-center gap-2">
            <span className={`w-1.5 h-1.5 rounded-full ${
              connectionStatus === 'connected' ? 'bg-green animate-pulse' :
              connectionStatus === 'polling' ? 'bg-amber' : 'bg-red/50'
            }`} />
            <span className="text-[10px] text-muted-foreground hidden sm:inline tracking-wide">
              {connectionStatus === 'connected' ? 'Live' : connectionStatus === 'polling' ? 'Polling' : 'Offline'}
            </span>
          </div>
        </div>
      </div>
    </nav>
  )
}
