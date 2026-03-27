'use client'

import { useWarRoom } from '@/contexts/war-room-context'
import { GlowCard } from '@/components/ui/spotlight-card'
import { Wallet } from 'lucide-react'
import { NumberTicker } from '@/components/ui/number-ticker'

export function BudgetCard() {
  const { budget } = useWarRoom()

  const percentUsed = budget.percent_used ?? 0
  const totalSpent = budget.total_spent ?? 0
  const remaining = budget.remaining ?? 800
  const exceeded = budget.exceeded ?? false

  const barColor = exceeded ? 'bg-red-500' : percentUsed > 80 ? 'bg-amber-500' : 'bg-green-500'
  const textColor = exceeded ? 'text-red-400' : percentUsed > 80 ? 'text-amber-400' : 'text-green-400'

  return (
    <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
      <div className="glass-card hud-panel rounded-xl p-8">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Wallet className="w-4 h-4 text-gold" />
            <span className="text-sm font-semibold text-foreground">Monthly Budget</span>
          </div>
          <span className={`text-xs font-mono ${textColor}`}>
            {percentUsed.toFixed(1)}% used
          </span>
        </div>

        {/* Progress bar */}
        <div className="w-full h-2 bg-white/5 rounded-full overflow-hidden mb-4">
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${Math.min(percentUsed, 100)}%` }}
          />
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Spent</span>
            <div className="text-lg font-bold text-foreground">
              $<NumberTicker value={totalSpent} className="text-foreground" />
            </div>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Remaining</span>
            <div className={`text-lg font-bold ${textColor}`}>
              $<NumberTicker value={remaining} className={textColor} />
            </div>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Status</span>
            <div className={`text-sm font-semibold mt-1 ${exceeded ? 'text-red-400' : 'text-green-400'}`}>
              {exceeded ? 'EXCEEDED' : 'On Track'}
            </div>
          </div>
        </div>
      </div>
    </GlowCard>
  )
}
