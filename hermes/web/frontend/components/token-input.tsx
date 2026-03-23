'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, KeyRound } from 'lucide-react'
import { CinematicBackdrop } from '@/components/cinematic-backdrop'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function TokenInput() {
  const [inputToken, setInputToken] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputToken.trim()) return
    
    setIsSubmitting(true)
    // Store token and reload with it
    sessionStorage.setItem('perseus_token', inputToken.trim())
    // Update URL with token
    const url = new URL(window.location.href)
    url.searchParams.set('token', inputToken.trim())
    window.location.href = url.toString()
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
          <div className="relative">
            <Input
              type="password"
              placeholder="Enter your access token"
              value={inputToken}
              onChange={(e) => setInputToken(e.target.value)}
              className="bg-muted/50 border-border focus:border-gold focus:ring-gold/20 h-12 pr-12"
            />
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <AlertTriangle className="w-5 h-5 text-muted-foreground/50" />
            </div>
          </div>
          
          <Button
            type="submit"
            disabled={!inputToken.trim() || isSubmitting}
            className="w-full h-12 bg-gold hover:bg-gold/90 text-background font-semibold"
          >
            {isSubmitting ? (
              'Authenticating...'
            ) : (
              <>
                Access War Room
                <ArrowRight className="w-4 h-4 ml-2" />
              </>
            )}
          </Button>
        </form>

        <div className="mt-6 pt-6 border-t border-border">
          <p className="text-xs text-muted-foreground text-center">
            Or add <code className="text-gold">?token=YOUR_TOKEN</code> to the URL
          </p>
        </div>
      </motion.div>
    </div>
  )
}
