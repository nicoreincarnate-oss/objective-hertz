'use client'

import dynamic from 'next/dynamic'
import { useToken } from '@/hooks/use-token'
import { StrategicView } from '@/components/strategic-view'
import { Brain, Zap, TrendingUp, Target, AlertTriangle } from 'lucide-react'
import { useState } from 'react'

/**
 * Intelligence (/intel) — Strategic brain
 * AI chat, MAGMA insights, pattern analysis.
 *
 * SpiralAnimation uses window.innerWidth at module load (SSR crash).
 * Loaded via dynamic import with ssr: false as a full-page ambient backdrop.
 *
 * hero-futuristic.tsx uses three/webgpu (WebGPU renderer) + three/tsl — requires
 * a bleeding-edge browser WebGPU implementation and will crash on most production
 * environments. Not used here.
 */

const SpiralAnimation = dynamic(
  () => import('@/components/ui/spiral-animation').then(m => ({ default: m.SpiralAnimation })),
  { ssr: false, loading: () => null }
)

const SUGGESTED_QUESTIONS = [
  { icon: TrendingUp, text: 'Pipeline velocity and expected MRR this month?' },
  { icon: Target, text: 'Which lead segments are converting best?' },
  { icon: Zap, text: 'Top 3 priorities in the next 24 hours to maximize revenue?' },
  { icon: AlertTriangle, text: 'Any bottlenecks or failure signals to know about?' },
]

export default function IntelPage() {
  const { token } = useToken()
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)

  return (
    <div className="relative space-y-6">
      {/* Spiral animation — full page ambient backdrop */}
      <div className="fixed inset-0 pointer-events-none opacity-[0.06] z-0">
        <SpiralAnimation />
      </div>

      {/* All content sits above the spiral */}
      <div className="relative z-10 space-y-6">
        {/* ── PAGE HEADER ─────────────────────────────────────────── */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2.5 mb-1.5">
              <Brain className="w-6 h-6 text-gold" />
              <h1 className="text-2xl font-bold text-foreground">Intelligence</h1>
            </div>
            <p className="text-sm text-muted-foreground max-w-lg">
              Strategic brain — AI-powered insights grounded in live pipeline data, MAGMA patterns, and agent telemetry.
            </p>
          </div>
          <div className="flex items-center gap-2 text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest shrink-0 mt-1">
            <span className="w-1.5 h-1.5 rounded-full bg-gold animate-pulse" />
            MAGMA ACTIVE
          </div>
        </div>

        {/* ── SUGGESTED QUESTIONS ───────────────────────────────── */}
        <div>
          <p className="text-[10px] font-medium text-muted-foreground/40 uppercase tracking-widest mb-3">
            Suggested
          </p>
          {/* Single row of 4 cards on desktop, 2x2 on mobile */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {SUGGESTED_QUESTIONS.map(({ icon: Icon, text }, i) => (
              <button
                key={i}
                className="text-left p-4 rounded-xl bg-card/80 border border-border/40 hover:border-border/70 hover:bg-card transition-all duration-200 backdrop-blur-sm group"
                onClick={() => setPendingQuestion(text)}
              >
                <div className="flex items-start gap-2.5">
                  <div className="w-7 h-7 rounded-lg bg-white/5 flex items-center justify-center shrink-0 mt-0.5 group-hover:bg-gold/10 transition-colors">
                    <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-gold transition-colors" />
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed group-hover:text-foreground transition-colors">
                    {text}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* ── STRATEGIC ADVISOR ─────────────────────────────────── */}
        <div>
          <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-widest mb-3">
            Strategic Advisor
          </p>

          {/*
           * StrategicView accepts only { token }. Pending question from suggested
           * questions is shown as a pre-fill hint above the chat component.
           */}
          {pendingQuestion && (
            <div className="mb-3 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gold/5 border border-gold/20 text-sm text-muted-foreground">
              <Zap className="w-3.5 h-3.5 text-gold shrink-0" />
              <span className="flex-1 truncate text-xs">{pendingQuestion}</span>
              <button
                className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground shrink-0 ml-2"
                onClick={() => setPendingQuestion(null)}
              >
                ✕
              </button>
            </div>
          )}
          <StrategicView token={token} />
        </div>
      </div>
    </div>
  )
}
