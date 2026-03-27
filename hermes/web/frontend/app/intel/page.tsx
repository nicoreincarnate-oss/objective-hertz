'use client'

import { useToken } from '@/hooks/use-token'
import { StrategicView } from '@/components/strategic-view'
import { Brain } from 'lucide-react'

/**
 * Intelligence (/intel) — Strategic brain
 * AI chat, MAGMA insights, pattern analysis.
 */
export default function IntelPage() {
  const { token } = useToken()

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 mb-2">
        <Brain className="w-6 h-6 text-gold" />
        <div>
          <h1 className="text-2xl font-bold text-foreground">Intelligence</h1>
          <p className="text-xs text-muted-foreground">Strategic brain — AI-powered insights grounded in pipeline data</p>
        </div>
      </div>

      {/* Strategic AI chat — full width on this page */}
      <StrategicView token={token} />
    </div>
  )
}
