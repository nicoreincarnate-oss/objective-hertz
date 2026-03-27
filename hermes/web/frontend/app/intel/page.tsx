'use client'

import { useToken, authHeaders } from '@/hooks/use-token'
import { StrategicView } from '@/components/strategic-view'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { Brain, Zap, TrendingUp, Target, AlertTriangle, BookOpen, Scale } from 'lucide-react'
import { useState, useEffect } from 'react'

/**
 * Intelligence (/intel) — Strategic brain
 * AI chat, MAGMA insights, learnings browser, decision audit trail.
 */

const SUGGESTED_QUESTIONS = [
  { icon: TrendingUp, text: 'Pipeline velocity and expected MRR this month?' },
  { icon: Target, text: 'Which lead segments are converting best?' },
  { icon: Zap, text: 'Top 3 priorities in the next 24 hours to maximize revenue?' },
  { icon: AlertTriangle, text: 'Any bottlenecks or failure signals to know about?' },
]

interface Learning {
  id: number
  category: string
  insight: string
  confidence: number
  created_at: string | null
}

interface Decision {
  id: number
  agent: string
  decision_type: string
  decision: string
  reasoning: string
  outcome: string | null
  created_at: string | null
}

export default function IntelPage() {
  const { token } = useToken()
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [learnings, setLearnings] = useState<Learning[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])

  useEffect(() => {
    if (!token) return
    const headers = authHeaders(token)

    fetch('/api/learnings?limit=20', { headers })
      .then(r => r.ok ? r.json() : [])
      .then(data => setLearnings(Array.isArray(data) ? data : []))
      .catch(() => {})

    fetch('/api/decisions?limit=20', { headers })
      .then(r => r.ok ? r.json() : [])
      .then(data => setDecisions(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [token])

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
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {SUGGESTED_QUESTIONS.map(({ icon: Icon, text }, i) => (
            <GlowCard
              key={i}
              customSize
              glowColor="blue"
              className="w-full p-0 cursor-pointer transition-all duration-200"
            >
              <button
                className="w-full text-left p-6 group cursor-pointer"
                onClick={() => setPendingQuestion(text)}
              >
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0 mt-0.5 group-hover:bg-gold/10 transition-colors">
                    <Icon className="w-4 h-4 text-muted-foreground group-hover:text-gold transition-colors" />
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
        {pendingQuestion && (
          <div className="mb-3 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gold/5 border border-gold/20 text-sm text-muted-foreground">
            <Zap className="w-3.5 h-3.5 text-gold shrink-0" />
            <span className="flex-1 truncate text-xs">{pendingQuestion}</span>
            <button
              className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground shrink-0 ml-2 cursor-pointer transition-all duration-200"
              onClick={() => setPendingQuestion(null)}
            >
              &times;
            </button>
          </div>
        )}
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <StrategicView token={token} />
        </GlowCard>
      </div>

      {/* ── LEARNINGS ─────────────────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          AI Learnings
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <div className="glass-card hud-panel rounded-xl p-8">
            <div className="flex items-center gap-2 mb-4">
              <BookOpen className="w-4 h-4 text-teal" />
              <span className="text-sm font-semibold text-foreground">Titan Learnings</span>
              <span className="text-xs text-muted-foreground ml-auto">{learnings.length} insights</span>
            </div>
            {learnings.length === 0 ? (
              <p className="text-xs text-muted-foreground/50 text-center py-6">No learnings yet — Titan generates these as it processes leads</p>
            ) : (
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {learnings.map((l) => (
                  <div key={l.id} className="flex items-start gap-3 p-3 rounded-lg bg-white/5 border border-border/20">
                    <div className="shrink-0 mt-0.5">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-mono uppercase ${
                        l.confidence >= 0.8 ? 'bg-green-500/10 text-green-400' :
                        l.confidence >= 0.5 ? 'bg-amber-500/10 text-amber-400' : 'bg-white/5 text-muted-foreground'
                      }`}>
                        {(l.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-foreground leading-relaxed">{l.insight}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[10px] text-muted-foreground/50">{l.category}</span>
                        {l.created_at && (
                          <span className="text-[10px] text-muted-foreground/30">
                            {new Date(l.created_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </GlowCard>
      </div>

      {/* ── DECISION AUDIT TRAIL ──────────────────────────────── */}
      <div>
        <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
          Decision Audit Trail
        </p>
        <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
          <div className="glass-card hud-panel rounded-xl p-8">
            <div className="flex items-center gap-2 mb-4">
              <Scale className="w-4 h-4 text-gold" />
              <span className="text-sm font-semibold text-foreground">Agent Decisions</span>
              <span className="text-xs text-muted-foreground ml-auto">{decisions.length} recorded</span>
            </div>
            {decisions.length === 0 ? (
              <p className="text-xs text-muted-foreground/50 text-center py-6">No decisions yet — agents log their autonomous choices here</p>
            ) : (
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {decisions.map((d) => (
                  <div key={d.id} className="p-3 rounded-lg bg-white/5 border border-border/20">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[10px] font-mono text-gold uppercase">{d.agent}</span>
                      <span className="text-[10px] text-muted-foreground/40">|</span>
                      <span className="text-[10px] text-muted-foreground">{d.decision_type}</span>
                      {d.outcome && (
                        <span className={`ml-auto text-[9px] px-1.5 py-0.5 rounded ${
                          d.outcome === 'success' ? 'bg-green-500/10 text-green-400' :
                          d.outcome === 'failure' ? 'bg-red-500/10 text-red-400' : 'bg-white/5 text-muted-foreground'
                        }`}>
                          {d.outcome}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-foreground">{d.decision}</p>
                    {d.reasoning && (
                      <p className="text-[10px] text-muted-foreground/60 mt-1 italic">{d.reasoning}</p>
                    )}
                    {d.created_at && (
                      <span className="text-[9px] text-muted-foreground/30 mt-1 block">
                        {new Date(d.created_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </GlowCard>
      </div>
    </div>
  )
}
