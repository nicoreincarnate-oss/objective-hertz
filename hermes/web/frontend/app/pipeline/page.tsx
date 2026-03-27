'use client'

import { useWarRoom } from '@/contexts/war-room-context'
import { LeadsTable } from '@/components/leads-table'
import {
  CheckCircle2,
  Circle,
  CircleDotDashed,
  CircleAlert,
} from 'lucide-react'
import { motion, AnimatePresence, LayoutGroup } from 'framer-motion'
import { useState } from 'react'

/**
 * Pipeline (/pipeline) — Revenue funnel
 * AgentPlan-style pipeline stages on the left, LeadsTable on the right.
 * lightning-split (Component) uses h-screen w-screen which conflicts with
 * DashboardShell layout, so we use a CSS split with the same visual intent.
 */

// 13 Titan pipeline stages mapped to AgentPlan task/subtask structure
const PIPELINE_STAGES = [
  {
    id: '1',
    title: 'Lead Ingestion',
    description: 'Ingest and normalize raw leads from all sources',
    status: 'in-progress',
    priority: 'high',
    level: 0,
    dependencies: [],
    subtasks: [
      { id: '1.1', title: 'Fetch from Instantly', description: 'Pull new leads via Instantly API', status: 'completed', priority: 'high' },
      { id: '1.2', title: 'Normalize fields', description: 'Map source fields to canonical schema', status: 'in-progress', priority: 'high' },
      { id: '1.3', title: 'Dedup check', description: 'Compare against existing leads in Postgres', status: 'pending', priority: 'medium' },
    ],
  },
  {
    id: '2',
    title: 'Enrichment',
    description: 'Enrich lead data with company, tech stack, and intent signals',
    status: 'in-progress',
    priority: 'high',
    level: 0,
    dependencies: [],
    subtasks: [
      { id: '2.1', title: 'Firecrawl scrape', description: 'Scrape company website for signals', status: 'completed', priority: 'high' },
      { id: '2.2', title: 'Tech stack detection', description: 'Identify CMS, hosting, and analytics', status: 'in-progress', priority: 'medium' },
      { id: '2.3', title: 'Score lead', description: 'Compute ICP fit score 0–100', status: 'pending', priority: 'high' },
    ],
  },
  {
    id: '3',
    title: 'Outreach',
    description: 'Generate and send personalized cold emails',
    status: 'pending',
    priority: 'high',
    level: 1,
    dependencies: ['1', '2'],
    subtasks: [
      { id: '3.1', title: 'Draft email', description: 'Generate personalized copy via Claude', status: 'pending', priority: 'high' },
      { id: '3.2', title: 'Human review gate', description: 'Flag for approval if review mode', status: 'pending', priority: 'high' },
      { id: '3.3', title: 'Send via Instantly', description: 'Dispatch email through campaign', status: 'pending', priority: 'medium' },
    ],
  },
  {
    id: '4',
    title: 'Follow-up Sequence',
    description: 'Automated multi-touch follow-up over 7 days',
    status: 'pending',
    priority: 'medium',
    level: 1,
    dependencies: ['3'],
    subtasks: [
      { id: '4.1', title: 'Day 2 follow-up', description: 'Value-add email with case study', status: 'pending', priority: 'medium' },
      { id: '4.2', title: 'Day 5 follow-up', description: 'Social proof + urgency', status: 'pending', priority: 'medium' },
      { id: '4.3', title: 'Day 7 breakup', description: 'Final attempt — close or archive', status: 'pending', priority: 'low' },
    ],
  },
  {
    id: '5',
    title: 'Response Handling',
    description: 'Detect, classify and route lead responses',
    status: 'pending',
    priority: 'high',
    level: 1,
    dependencies: ['3'],
    subtasks: [
      { id: '5.1', title: 'Detect reply', description: 'Poll Instantly for incoming replies', status: 'pending', priority: 'high' },
      { id: '5.2', title: 'Classify intent', description: 'LLM: interested / not now / unsubscribe', status: 'pending', priority: 'high' },
      { id: '5.3', title: 'Route to closer', description: 'Handoff warm leads to ClawdBot or human', status: 'pending', priority: 'high' },
    ],
  },
  {
    id: '6',
    title: 'Site Generation',
    description: 'Build and deploy personalized demo site for warm leads',
    status: 'pending',
    priority: 'medium',
    level: 2,
    dependencies: ['5'],
    subtasks: [
      { id: '6.1', title: 'Select template', description: 'Choose industry template from library', status: 'pending', priority: 'medium' },
      { id: '6.2', title: 'Personalize content', description: 'Inject company name, colors, copy', status: 'pending', priority: 'medium' },
      { id: '6.3', title: 'Deploy to Netlify', description: 'Publish and return preview URL', status: 'pending', priority: 'medium' },
    ],
  },
  {
    id: '7',
    title: 'Close',
    description: 'Convert interested leads to paying clients',
    status: 'pending',
    priority: 'high',
    level: 2,
    dependencies: ['5', '6'],
    subtasks: [
      { id: '7.1', title: 'Send proposal', description: 'Automated pricing and scope email', status: 'pending', priority: 'high' },
      { id: '7.2', title: 'Collect payment', description: 'Stripe invoice or Wise transfer', status: 'pending', priority: 'high' },
      { id: '7.3', title: 'Mark closed', description: 'Update lead status, trigger onboarding', status: 'pending', priority: 'high' },
    ],
  },
]

const statusIcon = (status: string, size = 'h-4 w-4') => {
  if (status === 'completed') return <CheckCircle2 className={`${size} text-green-500`} />
  if (status === 'in-progress') return <CircleDotDashed className={`${size} text-blue-400`} />
  if (status === 'need-help') return <CircleAlert className={`${size} text-yellow-400`} />
  return <Circle className={`${size} text-muted-foreground`} />
}

const statusBadgeClass = (status: string) => {
  if (status === 'completed') return 'bg-green-500/10 text-green-400'
  if (status === 'in-progress') return 'bg-blue-500/10 text-blue-400'
  if (status === 'need-help') return 'bg-yellow-500/10 text-yellow-400'
  if (status === 'failed') return 'bg-red-500/10 text-red-400'
  return 'bg-white/5 text-muted-foreground'
}

function PipelineAgentPlan({ pipeline }: { pipeline: Record<string, number> }) {
  const [expandedTasks, setExpandedTasks] = useState<string[]>(['1', '2'])

  const toggle = (id: string) =>
    setExpandedTasks(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    )

  // Overlay live stage counts from pipeline data onto static stages
  const stages = PIPELINE_STAGES.map(stage => {
    const pipelineKey = stage.title.toLowerCase().replace(/\s+/g, '_')
    const count = pipeline[pipelineKey] ?? pipeline[stage.title] ?? null
    return { ...stage, count }
  })

  return (
    <div className="bg-card border border-border/50 rounded-xl overflow-hidden flex flex-col h-full">
      <div className="p-6 border-b border-border/50 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Pipeline Stages</h2>
          <p className="text-[10px] text-muted-foreground mt-0.5">13-stage Titan revenue engine</p>
        </div>
        <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
          {stages.filter(s => s.status === 'completed').length}/{stages.length} done
        </span>
      </div>
      <div className="p-6 overflow-y-auto flex-1">
        <LayoutGroup>
          <ul className="space-y-0.5">
            {stages.map((stage, index) => {
              const isExpanded = expandedTasks.includes(stage.id)
              return (
                <motion.li
                  key={stage.id}
                  className={index !== 0 ? 'mt-1 pt-1' : ''}
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.04 }}
                >
                  {/* Stage row */}
                  <div
                    className="group flex items-center px-3 py-2 rounded-lg hover:bg-white/5 cursor-pointer transition-all duration-200"
                    onClick={() => toggle(stage.id)}
                  >
                    <div className="mr-2.5 flex-shrink-0">{statusIcon(stage.status)}</div>
                    <div className="flex min-w-0 flex-grow items-center justify-between">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-xs font-mono text-muted-foreground/40 shrink-0">
                          {stage.id.padStart(2, '0')}
                        </span>
                        <span className="text-sm font-medium text-foreground truncate">
                          {stage.title}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0 ml-2">
                        {stage.count !== null && (
                          <span className="text-[10px] font-mono bg-white/5 text-muted-foreground px-1.5 py-0.5 rounded">
                            {stage.count}
                          </span>
                        )}
                        <span className={`text-[10px] rounded px-1.5 py-0.5 ${statusBadgeClass(stage.status)}`}>
                          {stage.status}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Subtasks */}
                  <AnimatePresence mode="wait">
                    {isExpanded && (
                      <motion.div
                        className="relative overflow-hidden"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2 }}
                      >
                        <div className="absolute top-0 bottom-0 left-[20px] border-l border-dashed border-border/40" />
                        <ul className="mt-0.5 mb-1.5 ml-3 mr-2 space-y-0.5">
                          {stage.subtasks.map(sub => (
                            <li key={sub.id} className="flex items-center gap-2 pl-6 py-1 rounded-md hover:bg-white/5 cursor-pointer transition-all duration-200">
                              <div className="flex-shrink-0">{statusIcon(sub.status, 'h-3 w-3')}</div>
                              <span className={`text-xs ${sub.status === 'completed' ? 'line-through text-muted-foreground/50' : 'text-muted-foreground'}`}>
                                {sub.title}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.li>
              )
            })}
          </ul>
        </LayoutGroup>
      </div>
    </div>
  )
}

export default function PipelinePage() {
  const { pipeline, leads } = useWarRoom()

  return (
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Pipeline</h1>
        <p className="text-xs text-muted-foreground mt-1">Revenue funnel — manage leads, track progression</p>
      </div>

      {/*
       * 5/7 grid split: pipeline stages need less width, leads table needs more.
       * Both panes stretch to equal height (items-stretch) for visual balance.
       */}
      <div className="grid lg:grid-cols-12 gap-6 items-stretch min-h-[600px]">
        {/* Left (5 cols): AgentPlan-style pipeline stages */}
        <div className="lg:col-span-5 flex flex-col">
          <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
            Stage Progress
          </p>
          <div className="flex-1">
            <PipelineAgentPlan pipeline={pipeline ?? {}} />
          </div>
        </div>

        {/* Right (7 cols): Leads table */}
        <div className="lg:col-span-7 flex flex-col">
          <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
            Active Leads
          </p>
          <div className="flex-1">
            <LeadsTable leads={leads ?? []} />
          </div>
        </div>
      </div>
    </div>
  )
}
