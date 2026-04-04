'use client'

import { startTransition, useMemo, useState } from 'react'
import useSWR from 'swr'
import { formatDistanceToNowStrict } from 'date-fns'
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  Clock3,
  Loader2,
  Radar,
  RefreshCcw,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react'

import { useToken, authHeaders } from '@/hooks/use-token'
import { useWarRoom } from '@/contexts/war-room-context'
import {
  buildAutopilotSnapshot,
  getApprovalPriorityLabel,
  type ApprovalRecord,
  type TaskRecord,
} from '@/lib/autopilot'
import { HyperText } from '@/components/ui/hyper-text'
import { GlowCard } from '@/components/ui/spotlight-card'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'

interface ApprovalsResponse {
  pending: ApprovalRecord[]
  history: ApprovalRecord[]
  enabled: boolean
  error?: string
}

function createFetcher(token: string) {
  return async <T,>(url: string): Promise<T> => {
    const response = await fetch(url, {
      headers: authHeaders(token),
      cache: 'no-store',
    })

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`)
    }

    return response.json() as Promise<T>
  }
}

function humanize(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

function formatRelativeTime(value: string | null): string {
  if (!value) return 'Awaiting timestamp'

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Awaiting timestamp'

  return formatDistanceToNowStrict(date, { addSuffix: true })
}

function formatTaskContext(task: TaskRecord): string {
  const candidateKeys = ['client_name', 'business_name', 'company', 'lead_id', 'approval_id', 'reason']

  for (const key of candidateKeys) {
    const value = task.payload[key]
    if (typeof value === 'string' && value.trim()) return value
    if (typeof value === 'number') return `${humanize(key)} ${value}`
  }

  const firstDetail = Object.entries(task.payload).find(([, value]) =>
    typeof value === 'string' || typeof value === 'number'
  )

  if (!firstDetail) return 'Runtime task'
  return `${humanize(firstDetail[0])}: ${String(firstDetail[1])}`
}

function formatApprovalContext(approval: ApprovalRecord): string {
  const reason = approval.details.reason
  if (typeof reason === 'string' && reason.trim()) return reason

  const key = approval.details.key
  if (typeof key === 'string') {
    const value = approval.details.value
    return value === undefined
      ? humanize(key)
      : `${humanize(key)} -> ${String(value)}`
  }

  return `Requested by ${approval.requested_by}`
}

function isDaemonLive(lastHeartbeat: string | null, paused: boolean): boolean {
  if (paused || !lastHeartbeat) return false
  const elapsed = Date.now() - new Date(lastHeartbeat).getTime()
  return elapsed < 120_000
}

function statusTone(label: string): string {
  if (label === 'Operator attention required') return 'border-red-400/30 bg-red-500/10 text-red-200'
  if (label === 'Execution in flight') return 'border-amber-400/30 bg-amber-500/10 text-amber-100'
  return 'border-emerald-400/30 bg-emerald-500/10 text-emerald-100'
}

function MetricTile({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  detail: string
  icon: typeof Activity
}) {
  return (
    <Card className="border-border/50 bg-background/50 backdrop-blur-sm">
      <CardHeader className="px-5 pb-2">
        <CardDescription className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground/60">
          {label}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-3xl font-semibold text-foreground">{value}</div>
            <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
          </div>
          <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/10 p-2 text-cyan-200">
            <Icon className="h-4 w-4" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export function AutopilotConsole() {
  const { token } = useToken()
  const { taskQueue, daemons, events, health, connectionStatus } = useWarRoom()
  const [approvalActionKey, setApprovalActionKey] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  const fetcher = useMemo(() => (token ? createFetcher(token) : null), [token])

  const { data: tasks = [], isLoading: tasksLoading, mutate: mutateTasks } = useSWR<TaskRecord[]>(
    token && fetcher ? '/api/tasks?limit=24' : null,
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: false },
  )

  const {
    data: approvalsData,
    isLoading: approvalsLoading,
    mutate: mutateApprovals,
  } = useSWR<ApprovalsResponse>(
    token && fetcher ? '/api/approvals' : null,
    fetcher,
    {
      refreshInterval: 15000,
      revalidateOnFocus: false,
      fallbackData: { pending: [], history: [], enabled: false },
    },
  )

  const pendingApprovals = approvalsData?.pending ?? []
  const snapshot = useMemo(
    () => buildAutopilotSnapshot(tasks, pendingApprovals, taskQueue),
    [pendingApprovals, taskQueue, tasks],
  )

  const daemonEntries = useMemo(
    () => Object.entries(daemons).sort(([left], [right]) => left.localeCompare(right)),
    [daemons],
  )

  const liveDaemonCount = daemonEntries.filter(([, daemon]) =>
    isDaemonLive(daemon.last_heartbeat, daemon.paused)
  ).length

  const highSignalEvents = events.filter((event) =>
    ['approval_required', 'pipeline_error', 'review_needed', 'daemon_restart', 'daemon_control'].includes(event.event_type)
  )

  async function handleApprovalDecision(approvalId: string, decision: 'approved' | 'rejected') {
    if (!token) return

    const actionKey = `${approvalId}:${decision}`
    setApprovalActionKey(actionKey)

    try {
      const response = await fetch(`/api/approvals/${approvalId}/resolve`, {
        method: 'POST',
        headers: {
          ...authHeaders(token),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ decision, resolved_by: 'operator' }),
      })

      if (!response.ok) {
        throw new Error(`Approval resolution failed with ${response.status}`)
      }

      await Promise.all([mutateApprovals(), mutateTasks()])
      startTransition(() => {
        setFeedback(`${decision === 'approved' ? 'Approved' : 'Rejected'} ${approvalId.slice(0, 8)}`)
      })
    } catch {
      startTransition(() => {
        setFeedback('Approval action failed')
      })
    } finally {
      setApprovalActionKey(null)
      window.setTimeout(() => setFeedback(null), 2400)
    }
  }

  const pageLoading = tasksLoading && approvalsLoading

  return (
    <div className="space-y-8">
      {feedback && (
        <div className="fixed top-20 right-6 z-50 rounded-xl border border-cyan-400/30 bg-slate-950/85 px-4 py-2 text-sm text-cyan-100 shadow-2xl backdrop-blur-xl">
          {feedback}
        </div>
      )}

      <section className="relative overflow-hidden rounded-[28px] border border-cyan-400/15 bg-[radial-gradient(circle_at_top_left,_rgba(88,224,255,0.18),_transparent_34%),linear-gradient(135deg,rgba(8,12,20,0.96),rgba(10,18,32,0.8))] p-6 sm:p-8">
        <div className="absolute inset-y-0 right-0 hidden w-1/3 bg-[radial-gradient(circle_at_center,_rgba(255,255,255,0.12),_transparent_55%)] lg:block" />
        <div className="relative z-10 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl space-y-4">
            <Badge variant="outline" className="border-cyan-400/30 bg-cyan-400/10 text-cyan-100">
              <Radar className="h-3.5 w-3.5" />
              Hermes Autopilot
            </Badge>
            <div>
              <HyperText text="Autopilot" className="text-3xl font-bold text-white sm:text-4xl" />
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
                Live queue visibility for Hermes, Perseus, Titan, and ClawdBot. This is the first War Room
                control surface inspired by Scales, but wired to our own approvals, daemon state, and operator loop.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-300">
              <Badge className={statusTone(snapshot.focusLabel)}>{snapshot.focusLabel}</Badge>
              <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">
                Mode: {health?.mode ?? 'review'}
              </span>
              <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1">
                Sync: {connectionStatus}
              </span>
            </div>
          </div>

          <GlowCard customSize glowColor={snapshot.blockedTasks > 0 ? 'red' : 'blue'} className="w-full max-w-sm bg-transparent p-0 shadow-none">
            <div className="rounded-[24px] border border-white/10 bg-slate-950/70 p-5 backdrop-blur-xl">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Execution Completion</p>
                  <div className="mt-2 text-4xl font-semibold text-white">{snapshot.completionRate}%</div>
                </div>
                <div className="rounded-2xl border border-cyan-400/20 bg-cyan-400/10 p-3 text-cyan-100">
                  <Sparkles className="h-5 w-5" />
                </div>
              </div>
              <Progress value={snapshot.completionRate} className="mt-4 h-2.5 bg-white/10" />
              <p className="mt-3 text-xs text-slate-400">
                {snapshot.pendingApprovals > 0
                  ? `${snapshot.pendingApprovals} approvals are waiting on the operator loop.`
                  : 'Approval lane is clear for the moment.'}
              </p>
            </div>
          </GlowCard>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label="Queue Depth"
          value={String(snapshot.queueDepth)}
          detail={`${tasks.length} recent tasks loaded`}
          icon={Clock3}
        />
        <MetricTile
          label="Active Tasks"
          value={String(snapshot.activeTasks)}
          detail="Currently executing across daemons"
          icon={Activity}
        />
        <MetricTile
          label="Blocked Tasks"
          value={String(snapshot.blockedTasks)}
          detail={snapshot.blockedTasks > 0 ? 'Failures need intervention' : 'No blocked work detected'}
          icon={AlertTriangle}
        />
        <MetricTile
          label="Daemon Posture"
          value={`${liveDaemonCount}/${daemonEntries.length || 0}`}
          detail="Live heartbeat coverage"
          icon={Bot}
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.65fr_1fr]">
        <Card className="border-border/50 bg-background/55 backdrop-blur-md">
          <CardHeader className="border-b border-border/50">
            <CardTitle className="text-lg text-foreground">Execution Board</CardTitle>
            <CardDescription>Recent tasks grouped by live status lane.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 p-4 md:grid-cols-2">
            {snapshot.lanes.map((lane) => (
              <div key={lane.id} className="rounded-2xl border border-border/40 bg-black/10 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-foreground">{lane.label}</h3>
                    <p className="text-xs text-muted-foreground">{lane.count} tasks in lane</p>
                  </div>
                  <Badge variant="outline" className="border-border/50 bg-background/60 text-foreground">
                    {lane.count}
                  </Badge>
                </div>

                <div className="mt-4 space-y-2">
                  {lane.tasks.slice(0, 4).map((task) => (
                    <div key={task.id} className="rounded-xl border border-border/40 bg-background/50 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">{humanize(task.task_type)}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{formatTaskContext(task)}</p>
                        </div>
                        <Badge variant="outline" className="border-border/50 text-[10px] uppercase">
                          P{task.priority}
                        </Badge>
                      </div>
                      <p className="mt-2 text-[11px] text-muted-foreground">
                        Updated {formatRelativeTime(task.updated_at ?? task.created_at)}
                      </p>
                    </div>
                  ))}

                  {lane.tasks.length === 0 && (
                    <div className="rounded-xl border border-dashed border-border/40 bg-background/35 px-4 py-5 text-xs text-muted-foreground">
                      No tasks in this lane right now.
                    </div>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-lg text-foreground">Approvals</CardTitle>
                  <CardDescription>Governance gates that can be resolved from the War Room.</CardDescription>
                </div>
                {pageLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              {pendingApprovals.length === 0 && (
                <div className="rounded-2xl border border-dashed border-border/40 bg-background/35 px-4 py-5 text-sm text-muted-foreground">
                  No pending approvals. Hermes has a clear operator lane.
                </div>
              )}

              {pendingApprovals.map((approval) => {
                const priority = getApprovalPriorityLabel(approval)
                const actionBusy = approvalActionKey?.startsWith(approval.approval_id)
                return (
                  <div key={approval.approval_id} className="rounded-2xl border border-border/40 bg-background/50 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant="outline"
                        className={
                          priority === 'critical'
                            ? 'border-red-400/30 bg-red-500/10 text-red-100'
                            : priority === 'watch'
                              ? 'border-amber-400/30 bg-amber-500/10 text-amber-100'
                              : 'border-border/50 bg-background/60 text-foreground'
                        }
                      >
                        {priority}
                      </Badge>
                      <Badge variant="outline" className="border-border/50 bg-background/60 text-foreground">
                        {humanize(approval.type)}
                      </Badge>
                    </div>
                    <p className="mt-3 text-sm font-medium text-foreground">
                      {formatApprovalContext(approval)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {approval.approval_id.slice(0, 8)} • created {formatRelativeTime(approval.created_at)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Expires {formatRelativeTime(approval.expires_at)}
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        className="bg-emerald-500 text-slate-950 hover:bg-emerald-400"
                        disabled={actionBusy}
                        onClick={() => handleApprovalDecision(approval.approval_id, 'approved')}
                      >
                        {actionBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="border-red-400/25 bg-red-500/5 text-red-100 hover:bg-red-500/10"
                        disabled={actionBusy}
                        onClick={() => handleApprovalDecision(approval.approval_id, 'rejected')}
                      >
                        <X className="h-3.5 w-3.5" />
                        Reject
                      </Button>
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <CardTitle className="text-lg text-foreground">Daemon Posture</CardTitle>
              <CardDescription>Heartbeat and pause state across the active fleet.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              {daemonEntries.map(([name, daemon]) => {
                const live = isDaemonLive(daemon.last_heartbeat, daemon.paused)
                return (
                  <div key={name} className="flex items-center justify-between rounded-2xl border border-border/40 bg-background/50 px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-foreground">{humanize(name)}</p>
                      <p className="text-xs text-muted-foreground">
                        {daemon.paused ? 'Paused by operator' : `Heartbeat ${formatRelativeTime(daemon.last_heartbeat)}`}
                      </p>
                    </div>
                    <Badge
                      variant="outline"
                      className={
                        daemon.paused
                          ? 'border-amber-400/30 bg-amber-500/10 text-amber-100'
                          : live
                            ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-100'
                            : 'border-red-400/30 bg-red-500/10 text-red-100'
                      }
                    >
                      {daemon.paused ? 'Paused' : live ? 'Live' : 'Stale'}
                    </Badge>
                  </div>
                )
              })}

              {daemonEntries.length === 0 && (
                <div className="rounded-2xl border border-dashed border-border/40 bg-background/35 px-4 py-5 text-sm text-muted-foreground">
                  No daemon registry data yet.
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-background/55 backdrop-blur-md">
            <CardHeader className="border-b border-border/50">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-lg text-foreground">Recent Signals</CardTitle>
                  <CardDescription>High-signal events that matter to operator flow.</CardDescription>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground"
                  onClick={() => {
                    void mutateTasks()
                    void mutateApprovals()
                  }}
                >
                  <RefreshCcw className="h-3.5 w-3.5" />
                  Refresh
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              {highSignalEvents.slice(0, 5).map((event) => (
                <div key={event.id} className="rounded-2xl border border-border/40 bg-background/50 p-4">
                  <div className="flex items-center gap-2">
                    <ShieldAlert className="h-4 w-4 text-cyan-200" />
                    <p className="text-sm font-medium text-foreground">{humanize(event.event_type)}</p>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {typeof event.payload?.lead === 'string'
                      ? event.payload.lead
                      : JSON.stringify(event.payload).slice(0, 96) || 'No payload'}
                  </p>
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    Logged {formatRelativeTime(event.created_at)}
                  </p>
                </div>
              ))}

              {highSignalEvents.length === 0 && (
                <div className="rounded-2xl border border-dashed border-border/40 bg-background/35 px-4 py-5 text-sm text-muted-foreground">
                  No high-signal events in the current sync window.
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </section>
    </div>
  )
}
