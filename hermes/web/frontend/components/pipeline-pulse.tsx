'use client'

import React, { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { TrendingUp, ChevronRight, ArrowDown } from 'lucide-react'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { AnimatedBeam } from '@/components/ui/animated-beam'

interface PipelinePulseProps {
  data: Record<string, number>
  leads?: Array<{
    id: number
    business_name: string
    email: string
    industry: string
    status: string
    lead_score: number
    created_at: string | null
  }>
}

const STAGES = [
  { key: 'discovered', label: 'Discovered', color: '#58e0ff', icon: '/assets/generated/pipeline/discovered.svg' },
  { key: 'researched', label: 'Researched', color: '#4dd4f0', icon: '/assets/generated/pipeline/researched.svg' },
  { key: 'email_drafted', label: 'Drafted', color: '#42c8e0', icon: '/assets/generated/pipeline/email_drafted.svg' },
  { key: 'email_queued', label: 'Queued', color: '#39f3e2', icon: '/assets/generated/pipeline/email_queued.svg' },
  { key: 'email_sent', label: 'Sent', color: '#39e0c8', icon: '/assets/generated/pipeline/email_sent.svg' },
  { key: 'replied', label: 'Replied', color: '#3fd8a0', icon: '/assets/generated/pipeline/replied.svg' },
  { key: 'interested', label: 'Interested', color: '#62f1b5', icon: '/assets/generated/pipeline/interested.svg' },
  { key: 'demo_built', label: 'Demo Built', color: '#7ef0a0', icon: '/assets/generated/pipeline/demo_built.svg' },
  { key: 'proposal_sent', label: 'Proposal', color: '#a0e878', icon: '/assets/generated/pipeline/proposal_sent.svg' },
  { key: 'closed', label: 'Closed', color: '#c8e060', icon: '/assets/generated/pipeline/closed.svg' },
  { key: 'deployed', label: 'Deployed', color: '#e0d050', icon: '/assets/generated/pipeline/deployed.svg' },
  { key: 'invoiced', label: 'Invoiced', color: '#ffb347', icon: '/assets/generated/pipeline/invoiced.svg' },
  { key: 'paid', label: 'Paid', color: '#ff9040', icon: '/assets/generated/pipeline/paid.svg' },
] as const

export function PipelinePulse({ data, leads = [] }: PipelinePulseProps) {
  const [selectedStage, setSelectedStage] = useState<string | null>(null)

  // Refs for AnimatedBeam: one container + one ref per stage node
  const containerRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const stageRefs = useRef<Array<React.RefObject<HTMLElement>>>(
    STAGES.map(() => React.createRef<HTMLElement>() as React.RefObject<HTMLElement>)
  )

  const total = Object.values(data).reduce((a, b) => a + (typeof b === 'number' ? b : 0), 0)
  const maxValue = Math.max(...Object.values(data).filter((v): v is number => typeof v === 'number'), 1)
  const discoveredCount = (data['discovered'] ?? 0) || 1
  const paidCount = data['paid'] ?? 0
  const conversionRate = ((paidCount / discoveredCount) * 100).toFixed(2)

  const stageLeads = selectedStage ? leads.filter(l => l.status === selectedStage) : []
  const selectedStageDef = STAGES.find(s => s.key === selectedStage)

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.4 }}
        className="glass-card-elevated hud-panel rounded-xl p-5 md:p-6"
      >
        <h2 className="text-lg font-semibold text-foreground mb-5 flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-gold" />
          Pipeline Pulse
          <span className="text-xs text-muted-foreground ml-auto">{total} leads total</span>
        </h2>

        {/* Flow visualization — container ref needed for AnimatedBeam positioning */}
        <div ref={containerRef} className="relative space-y-1">
          {STAGES.map((stage, index) => {
            const value = data[stage.key] ?? 0
            const nextValue = index < STAGES.length - 1 ? (data[STAGES[index + 1].key] ?? 0) : 0
            const percentage = maxValue > 0 ? (value / maxValue) * 100 : 0
            const convFromPrev = index > 0 && (data[STAGES[index - 1].key] ?? 0) > 0
              ? ((value / (data[STAGES[index - 1].key] ?? 1)) * 100).toFixed(0)
              : null

            return (
              <div key={stage.key}>
                {/* Stage node — attach ref to icon so beam connects icon-to-icon */}
                <motion.button
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.4, delay: index * 0.05 }}
                  onClick={() => setSelectedStage(stage.key)}
                  className="group w-full flex items-center gap-3 py-1.5 hover:bg-white/[0.03] rounded-lg px-2 -mx-2 transition-colors cursor-pointer"
                >
                  {/* Stage icon — ref target for AnimatedBeam */}
                  <div ref={stageRefs.current[index] as React.RefObject<HTMLDivElement>} className="w-5 h-5 shrink-0 flex items-center justify-center">
                    <img
                      src={stage.icon}
                      alt={stage.label}
                      className="w-5 h-5"
                      style={{ opacity: value > 0 ? 1 : 0.3 }}
                    />
                  </div>

                  {/* Label */}
                  <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors w-20 text-left shrink-0">
                    {stage.label}
                  </span>

                  {/* Progress bar */}
                  <div className="flex-1 relative h-3 bg-muted/30 rounded-full overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${percentage}%` }}
                      transition={{ duration: 0.8, delay: index * 0.05, ease: 'easeOut' }}
                      className="absolute inset-y-0 left-0 rounded-full"
                      style={{
                        background: `linear-gradient(90deg, ${stage.color}cc, ${stage.color})`,
                        boxShadow: `0 0 12px ${stage.color}30`,
                      }}
                    />
                  </div>

                  {/* Count */}
                  <span className="text-sm font-semibold text-foreground tabular-nums w-8 text-right shrink-0">
                    {value}
                  </span>

                  {/* Conversion from previous */}
                  {convFromPrev && (
                    <span className="text-[10px] text-muted-foreground w-10 text-right shrink-0">
                      {convFromPrev}%
                    </span>
                  )}

                  <ChevronRight className="w-3.5 h-3.5 text-muted-foreground opacity-0 group-hover:opacity-60 transition-opacity shrink-0" />
                </motion.button>

                {/* Connector line replaced by AnimatedBeam between this icon and the next */}
                {index < STAGES.length - 1 && containerRef.current && stageRefs.current[index].current && stageRefs.current[index + 1].current && (
                  <AnimatedBeam
                    containerRef={containerRef as React.RefObject<HTMLElement>}
                    fromRef={stageRefs.current[index]}
                    toRef={stageRefs.current[index + 1]}
                    gradientStartColor={stage.color}
                    gradientStopColor={STAGES[index + 1].color}
                    pathColor={stage.color}
                    pathOpacity={0.15}
                    pathWidth={1}
                    duration={3}
                    delay={index * 0.15}
                    curvature={0}
                  />
                )}
              </div>
            )
          })}
        </div>

        {/* Summary */}
        <div className="mt-5 pt-4 border-t border-border flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Discovery → Paid</span>
          <span className="text-sm font-semibold text-gold">{conversionRate}% conversion</span>
        </div>
      </motion.div>

      {/* Stage detail sheet */}
      <Sheet open={!!selectedStage} onOpenChange={(open) => !open && setSelectedStage(null)}>
        <SheetContent className="glass-card-elevated border-l border-border">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2">
              {selectedStageDef && (
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: selectedStageDef.color }}
                />
              )}
              {selectedStageDef?.label ?? 'Stage'} — {data[selectedStage ?? ''] ?? 0} leads
            </SheetTitle>
            <SheetDescription>
              Leads currently at this pipeline stage
            </SheetDescription>
          </SheetHeader>

          <ScrollArea className="h-[calc(100vh-120px)] mt-4">
            {stageLeads.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground text-sm">
                No leads at this stage{leads.length === 0 ? ' (lead data not loaded)' : ''}
              </div>
            ) : (
              <div className="space-y-3">
                {stageLeads.map((lead) => (
                  <div
                    key={lead.id}
                    className="glass-card rounded-lg p-3 space-y-2"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-foreground">{lead.business_name}</span>
                      <Badge variant="outline" className="text-[10px]">
                        Score: {lead.lead_score}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {lead.email} • {lead.industry}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </>
  )
}
