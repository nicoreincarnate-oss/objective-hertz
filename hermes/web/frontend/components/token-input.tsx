'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import { motion } from 'framer-motion'
import { ArrowRight, KeyRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

/**
 * TokenInput — Login screen
 * SpiralAnimation as full-screen ambient background.
 * CinematicBackdrop already renders in DashboardShell, so here we use
 * SpiralAnimation as a premium dark login backdrop.
 */

const SpiralAnimation = dynamic(
  () => import('@/components/ui/spiral-animation').then(m => ({ default: m.SpiralAnimation })),
  { ssr: false, loading: () => null }
)

interface TokenInputProps {
  onTokenSubmit: (token: string) => void
}

export function TokenInput({ onTokenSubmit }: TokenInputProps) {
  const [inputToken, setInputToken] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputToken.trim()) return
    onTokenSubmit(inputToken.trim())
  }

  return (
    <div className="relative min-h-screen bg-background flex items-center justify-center overflow-hidden p-6">
      {/* SpiralAnimation — full-screen ambient backdrop */}
      <div className="absolute inset-0 opacity-20 pointer-events-none">
        <SpiralAnimation />
      </div>

      {/* Radial vignette so edges recede */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 70% 70% at 50% 50%, transparent 30%, rgba(4,8,12,0.85) 100%)',
        }}
      />

      {/* Login card */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.23, 1, 0.32, 1] }}
        className="relative z-10 w-full max-w-sm"
      >
        {/* PERSEUS badge above card */}
        <div className="flex justify-center mb-5">
          <div className="flex items-center gap-2 px-4 py-1.5 rounded-full bg-gold/10 border border-gold/25">
            <span className="w-1.5 h-1.5 rounded-full bg-gold animate-pulse" />
            <span className="text-xs font-bold tracking-[0.25em] text-gold">PERSEUS</span>
          </div>
        </div>

        {/* Glass card */}
        <div className="glass-card hud-panel rounded-2xl p-8 border border-border/60 bg-card/70 backdrop-blur-xl shadow-2xl">
          <div className="text-center mb-8">
            <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-gold/10 flex items-center justify-center border border-gold/25 shadow-lg shadow-gold/5">
              <KeyRound className="w-8 h-8 text-gold" />
            </div>
            <h1 className="text-xl font-bold text-foreground mb-2 tracking-tight">War Room Access</h1>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Enter your access token to unlock the operational dashboard
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              type="password"
              placeholder="Enter access token"
              value={inputToken}
              onChange={(e) => setInputToken(e.target.value)}
              className="bg-muted/40 border-border/60 focus:border-gold focus:ring-gold/20 h-12 text-sm"
              autoComplete="current-password"
              autoFocus
            />

            <Button
              type="submit"
              disabled={!inputToken.trim()}
              className="w-full h-12 bg-gold hover:bg-gold/90 text-background font-semibold tracking-wide text-sm cursor-pointer transition-all duration-200"
            >
              Access War Room
              <ArrowRight className="w-4 h-4 ml-2" />
            </Button>
          </form>
        </div>
      </motion.div>
    </div>
  )
}
