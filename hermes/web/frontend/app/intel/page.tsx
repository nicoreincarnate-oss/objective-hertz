'use client'

import { useToken } from '@/hooks/use-token'
import { StrategicView } from '@/components/strategic-view'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { Brain, Zap, TrendingUp, Target, AlertTriangle } from 'lucide-react'
import { useState } from 'react'

/**
 * Intelligence (/intel) — Strategic brain
 * AI chat, MAGMA insights, pattern analysis.
 */

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
    <div className="space-y-8">
      {/* ── PAGE HEADER ─────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2.5 mb-1.5">
            <Brain className="w-6 h-6 text-gold" />
            <HyperText text="Intelligence" className="text-2xl font-bold text-foreground" />
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
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Suggested
        </p>
        {/* Single row of 4 cards on desktop, 2x2 on mobile */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {SUGGESTED_QUESTIONS.map(({ icon: Icon, text }, i) => (
            <GlowCard
              key={i}
              customSize
              glowColor="blue"
              className="w-full p-0 cursor-pointer transition-all duration-200"
            >
              <button
                className="w-full text-left p-5 group cursor-pointer"
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
            </GlowCard>
          ))}
        </div>
      </div>

      {/* ── STRATEGIC ADVISOR ─────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
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
              className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground shrink-0 ml-2 cursor-pointer transition-all duration-200"
              onClick={() => setPendingQuestion(null)}
            >
              ✕
            </button>
          </div>
        )}
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <StrategicView token={token} />
        </GlowCard>
      </div>
    </div>
  )
}
