'use client'

import { useWarRoom } from '@/contexts/war-room-context'
import { useToken } from '@/hooks/use-token'
import { PipelinePulse } from '@/components/pipeline-pulse'
import { LeadsTable } from '@/components/leads-table'

/**
 * Pipeline (/pipeline) — Revenue funnel
 * Manage leads, track progression, take action.
 */
export default function PipelinePage() {
  const { pipeline, leads } = useWarRoom()

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-2xl font-bold text-foreground">Pipeline</h1>
        <span className="text-xs text-muted-foreground">Revenue funnel — manage leads, track progression</span>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Pipeline flow */}
        <PipelinePulse data={pipeline ?? {}} leads={leads ?? []} />

        {/* Leads table */}
        <LeadsTable leads={leads ?? []} />
      </div>
    </div>
  )
}
