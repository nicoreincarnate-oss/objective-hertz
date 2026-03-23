'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Brain, Loader2, Search } from 'lucide-react'

interface InsightResponse {
  answer: string
  evidence: string[]
  recommended_actions: string[]
}

export function StrategicView({ token }: { token: string | null }) {
  const [question, setQuestion] = useState('Why did restaurants convert better than salons?')
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState<InsightResponse | null>(null)
  const [error, setError] = useState('')

  const askQuestion = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token || !question.trim() || isLoading) return

    setIsLoading(true)
    setError('')

    try {
      const res = await fetch(`/api/insights?token=${token}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })

      const data = await res.json()
      if (!res.ok) {
        setError(data.error || 'Strategic view unavailable.')
        setResult(null)
        return
      }

      setResult(data)
    } catch {
      setError('Strategic view unavailable.')
      setResult(null)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.35 }}
      className="glass-card hud-panel rounded-xl p-5 md:p-6"
    >
      <h2 className="text-lg font-semibold text-foreground mb-2 flex items-center gap-2">
        <Brain className="w-5 h-5 text-gold" />
        God&apos;s-Eye View
      </h2>
      <p className="text-sm text-muted-foreground mb-4">
        Ask Hermes to explain campaign performance using real pipeline data and recent learnings.
      </p>

      <form onSubmit={askQuestion} className="space-y-4">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
          className="w-full bg-muted/50 border border-border rounded-lg px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-gold/40 focus:ring-1 focus:ring-gold/20 resize-none"
        />
        <button
          type="submit"
          disabled={!token || !question.trim() || isLoading}
          className="w-full py-3 px-4 rounded-lg bg-gradient-to-r from-gold to-gold-dim text-background font-semibold flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-gold/20 transition-shadow"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          {isLoading ? 'Thinking...' : 'Ask Hermes'}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-red">{error}</p>}

      {result && (
        <div className="mt-5 space-y-4">
          <div className="rounded-lg bg-muted/30 border border-border p-4">
            <p className="text-sm text-foreground leading-6">{result.answer}</p>
          </div>

          {result.evidence.length > 0 && (
            <div>
              <h3 className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Evidence</h3>
              <div className="space-y-2">
                {result.evidence.map((item) => (
                  <div key={item} className="rounded-lg bg-muted/20 border border-border px-3 py-2 text-sm text-foreground">
                    {item}
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.recommended_actions.length > 0 && (
            <div>
              <h3 className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Recommended Actions</h3>
              <div className="space-y-2">
                {result.recommended_actions.map((item) => (
                  <div key={item} className="rounded-lg bg-gold/5 border border-gold/20 px-3 py-2 text-sm text-foreground">
                    {item}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
