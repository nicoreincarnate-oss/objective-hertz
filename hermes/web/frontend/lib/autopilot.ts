export interface TaskRecord {
  id: number
  task_type: string
  payload: Record<string, unknown>
  priority: number
  status: string
  created_at: string | null
  updated_at: string | null
}

export interface ApprovalRecord {
  approval_id: string
  type: string
  status: string
  requested_by: string
  details: Record<string, unknown>
  created_at: string | null
  expires_at: string | null
}

export interface TaskLane {
  id: string
  label: string
  count: number
  tasks: TaskRecord[]
}

export interface AutopilotSnapshot {
  queueDepth: number
  activeTasks: number
  blockedTasks: number
  pendingApprovals: number
  criticalApprovals: number
  completionRate: number
  focusLabel: string
  lanes: TaskLane[]
}

const LANE_ORDER = ['queued', 'running', 'completed', 'failed'] as const

const LANE_LABELS: Record<(typeof LANE_ORDER)[number], string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Blocked',
}

const STATUS_ALIASES: Record<string, (typeof LANE_ORDER)[number]> = {
  pending: 'queued',
  queued: 'queued',
  scheduled: 'queued',
  running: 'running',
  in_progress: 'running',
  active: 'running',
  completed: 'completed',
  success: 'completed',
  done: 'completed',
  failed: 'failed',
  error: 'failed',
  blocked: 'failed',
}

function normalizeTaskStatus(status: string): (typeof LANE_ORDER)[number] {
  return STATUS_ALIASES[status.toLowerCase()] ?? 'queued'
}

function minutesUntilExpiry(expiresAt: string | null, referenceTime: number): number | null {
  if (!expiresAt) return null
  const expires = Date.parse(expiresAt)
  if (Number.isNaN(expires)) return null
  return Math.round((expires - referenceTime) / 60000)
}

function approvalIsCritical(approval: ApprovalRecord, referenceTime: number): boolean {
  if (approval.type === 'autonomy_transition' || approval.type === 'budget_override') {
    return true
  }

  const minutesRemaining = minutesUntilExpiry(approval.expires_at, referenceTime)
  return minutesRemaining !== null && minutesRemaining <= 90
}

function taskSortValue(task: TaskRecord): number {
  const created = task.created_at ? Date.parse(task.created_at) : 0
  return Number.isNaN(created) ? 0 : created
}

export function groupTasksByStatus(tasks: TaskRecord[]): TaskLane[] {
  const grouped = new Map<(typeof LANE_ORDER)[number], TaskRecord[]>()

  for (const lane of LANE_ORDER) {
    grouped.set(lane, [])
  }

  for (const task of tasks) {
    grouped.get(normalizeTaskStatus(task.status))?.push(task)
  }

  return LANE_ORDER.map((lane) => ({
    id: lane,
    label: LANE_LABELS[lane],
    count: grouped.get(lane)?.length ?? 0,
    tasks: [...(grouped.get(lane) ?? [])].sort((left, right) => {
      if (left.priority !== right.priority) {
        return left.priority - right.priority
      }
      return taskSortValue(right) - taskSortValue(left)
    }),
  }))
}

export function buildAutopilotSnapshot(
  tasks: TaskRecord[],
  approvals: ApprovalRecord[],
  taskSummary: Record<string, number>,
  referenceTime = Date.now(),
): AutopilotSnapshot {
  const lanes = groupTasksByStatus(tasks)
  const queuedCount = taskSummary.queued ?? taskSummary.pending ?? lanes[0].count
  const runningCount = taskSummary.running ?? taskSummary.in_progress ?? lanes[1].count
  const completedCount = taskSummary.completed ?? taskSummary.done ?? lanes[2].count
  const failedCount = taskSummary.failed ?? taskSummary.blocked ?? lanes[3].count

  const queueDepth = Object.values(taskSummary).reduce((sum, value) => sum + value, 0)
  const activeTasks = runningCount
  const blockedTasks = failedCount
  const pendingApprovals = approvals.length
  const criticalApprovals = approvals.filter((approval) => approvalIsCritical(approval, referenceTime)).length
  const completionBase = completedCount + queuedCount + runningCount + failedCount
  const completionRate = completionBase > 0 ? Math.round((completedCount / completionBase) * 100) : 0

  let focusLabel = 'Autopilot stable'
  if (blockedTasks > 0 || criticalApprovals > 0) {
    focusLabel = 'Operator attention required'
  } else if (pendingApprovals > 0 || activeTasks > 0) {
    focusLabel = 'Execution in flight'
  }

  return {
    queueDepth,
    activeTasks,
    blockedTasks,
    pendingApprovals,
    criticalApprovals,
    completionRate,
    focusLabel,
    lanes,
  }
}

export function getApprovalPriorityLabel(
  approval: ApprovalRecord,
  referenceTime = Date.now(),
): 'critical' | 'watch' | 'normal' {
  if (approvalIsCritical(approval, referenceTime)) return 'critical'
  if (approval.type === 'config_change') return 'watch'
  return 'normal'
}
