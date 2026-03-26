'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, KeyRound } from 'lucide-react'
import { CinematicBackdrop } from '@/components/cinematic-backdrop'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

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
    <div className="relative min-h-screen bg-background aurora-bg noise-overlay flex items-center justify-center overflow-hidden p-6">
      <CinematicBackdrop priority />
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="relative z-10 glass-card hud-panel rounded-2xl p-8 max-w-md w-full"
      >
        <div className="text-center mb-8">
          <div className="w-16 h-16 mx-auto mb-6 rounded-full bg-gold/10 flex items-center justify-center border border-gold/30">
            <KeyRound className="w-8 h-8 text-gold" />
          </div>
          <h1 className="text-2xl font-bold text-foreground mb-3">PERSEUS War Room</h1>
          <p className="text-muted-foreground">
            Enter your access token to continue
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            type="password"
            placeholder="Enter your access token"
            value={inputToken}
            onChange={(e) => setInputToken(e.target.value)}
            className="bg-muted/50 border-border focus:border-gold focus:ring-gold/20 h-12"
            autoComplete="current-password"
          />

          <Button
            type="submit"
            disabled={!inputToken.trim()}
            className="w-full h-12 bg-gold hover:bg-gold/90 text-background font-semibold"
          >
            Access War Room
            <ArrowRight className="w-4 h-4 ml-2" />
          </Button>
        </form>
      </motion.div>
    </div>
  )
}
