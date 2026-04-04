'use client'

import { useToken } from '@/hooks/use-token'
import { WarRoomProvider } from '@/contexts/war-room-context'
import { WarRoomNav } from '@/components/war-room-nav'
import { CinematicBackdrop } from '@/components/cinematic-backdrop'
import { TokenInput } from '@/components/token-input'
import { Loader2 } from 'lucide-react'
import { motion } from 'framer-motion'
import { usePathname } from 'next/navigation'

/**
 * Dashboard shell: wraps all authenticated pages with:
 * - Token auth gate (shows login if no token)
 * - WarRoomProvider (WebSocket + SWR data)
 * - CinematicBackdrop (animated background)
 * - WarRoomNav (persistent nav bar)
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const { token, isLoading: tokenLoading, setToken } = useToken()
  const pathname = usePathname()
  const isOverlayRoute = pathname === '/buddy'

  if (tokenLoading) {
    if (isOverlayRoute) {
      return <div className="min-h-screen bg-transparent" />
    }

    return (
      <div className="relative min-h-screen bg-background flex items-center justify-center overflow-hidden">
        <CinematicBackdrop />
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="relative z-10 flex flex-col items-center gap-4"
        >
          <Loader2 className="w-10 h-10 text-gold animate-spin" />
          <p className="text-muted-foreground text-xs">Initializing PERSEUS...</p>
        </motion.div>
      </div>
    )
  }

  if (!token) {
    if (isOverlayRoute) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-transparent px-4 text-center text-xs text-cyan-50/80">
          PERSEUS desktop overlay is waiting for a valid session token.
        </div>
      )
    }

    return <TokenInput onTokenSubmit={setToken} />
  }

  if (isOverlayRoute) {
    return (
      <WarRoomProvider>
        <div className="min-h-screen overflow-hidden bg-transparent">
          {children}
        </div>
      </WarRoomProvider>
    )
  }

  return (
    <WarRoomProvider>
      <div className="relative min-h-screen bg-background overflow-hidden">
        <CinematicBackdrop />
        <div className="relative z-10">
          <WarRoomNav />
          <main className="max-w-7xl mx-auto px-5 sm:px-8 lg:px-10 py-8">
            {children}
          </main>
        </div>
      </div>
    </WarRoomProvider>
  )
}
