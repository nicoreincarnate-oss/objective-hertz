'use client'

import dynamic from 'next/dynamic'
import { useToken } from '@/hooks/use-token'
import { StrategicView } from '@/components/strategic-view'
import { GlowCard } from '@/components/ui/spotlight-card'
import { Brain, Zap, TrendingUp, Target, AlertTriangle } from 'lucide-react'
import { useState } from 'react'

/**
 * Intelligence (/intel) — Strategic brain
 * AI chat, MAGMA insights, pattern analysis.
 *
 * hero-futuristic.tsx uses three/webgpu (WebGPU renderer) + three/tsl — requires
 * a bleeding-edge browser WebGPU implementation and will crash on most production
 * environments. Skipped in favor of a functional intel header.
 *
 * SpiralAnimation uses window.innerWidth at module load (SSR crash).
 * Loaded via dynamic import with ssr: false as a loading state decoration.
 */

const SpiralAnimation = dynamic(
  () => import('@/components/ui/spiral-animation').then(m => ({ default: m.SpiralAnimation })),
  { ssr: false, loading: () => <div className="w-full h-full bg-black rounded-xl" /> }
)

const SUGGESTED_QUESTIONS = [
  { icon: TrendingUp, text: 'What\'s the current pipeline velocity and expected MRR this month?' },
  { icon: Target, text: 'Which lead segments are converting best — what patterns do you see?' },
  { icon: Zap, text: 'What should the team prioritize in the next 24 hours to maximize revenue?' },
  { icon: AlertTriangle, text: 'Are there any bottlenecks or failure signals I should know about?' },
]

export default function IntelPage() {
  const { token } = useToken()
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)

  return (
    <div className="space-y-6">
      {/* Page header — ambient spiral visual */}
      <div className="relative rounded-2xl border border-border/50 bg-card overflow-hidden" style={{ minHeight: 160 }}>
        {/* Spiral animation as background visual */}
        <div className="absolute inset-0 opacity-30 pointer-events-none">
          <SpiralAnimation />
        </div>

        {/* Header content */}
        <div className="relative z-10 p-6 flex items-end justify-between h-full">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Brain className="w-7 h-7 text-gold" />
              <h1 className="text-2xl font-bold text-foreground">Intelligence</h1>
            </div>
            <p className="text-sm text-muted-foreground max-w-md">
              Strategic brain — AI-powered insights grounded in live pipeline data, MAGMA patterns, and agent telemetry.
            </p>
          </div>
          <div className="flex items-center gap-2 text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest">
            <span className="w-1.5 h-1.5 rounded-full bg-gold animate-pulse" />
            MAGMA ACTIVE
          </div>
        </div>
      </div>

      {/* Suggested questions — SpotlightCard wrapped insight cards */}
      <div>
        <p className="text-[10px] text-muted-foreground/60 uppercase tracking-widest mb-3">Suggested Questions</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {SUGGESTED_QUESTIONS.map(({ icon: Icon, text }, i) => (
            <GlowCard
              key={i}
              glowColor={i % 2 === 0 ? 'purple' : 'blue'}
              customSize
              className="cursor-pointer p-0 bg-transparent border-border/30 hover:border-border/60 transition-colors"
            >
              <button
                className="w-full h-full text-left p-4 rounded-2xl"
                onClick={() => setPendingQuestion(text)}
              >
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0 mt-0.5">
                    <Icon className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <p className="text-sm text-muted-foreground leading-relaxed">{text}</p>
                </div>
              </button>
            </GlowCard>
          ))}
        </div>
      </div>

      {/* Strategic AI chat — main interface */}
      <div>
        <p className="text-[10px] text-muted-foreground/60 uppercase tracking-widest mb-3">Strategic Advisor</p>
        {/*
         * StrategicView accepts only { token }. Pending question from suggested
         * questions is shown as a pre-fill hint above the chat component.
         */}
        {pendingQuestion && (
          <div className="mb-3 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white/5 border border-border/40 text-sm text-muted-foreground">
            <Zap className="w-3.5 h-3.5 text-gold shrink-0" />
            <span className="flex-1 truncate">Suggested: {pendingQuestion}</span>
            <button
              className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground"
              onClick={() => setPendingQuestion(null)}
            >
              ✕
            </button>
          </div>
        )}
        <StrategicView token={token} />
      </div>
    </div>
  )
}
