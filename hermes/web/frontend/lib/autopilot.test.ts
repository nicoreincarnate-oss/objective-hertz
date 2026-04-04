import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildAutopilotSnapshot,
  groupTasksByStatus,
  type ApprovalRecord,
  type TaskRecord,
} from './autopilot.ts'

test('groupTasksByStatus orders tasks into execution lanes', () => {
  const tasks: TaskRecord[] = [
    { id: 1, task_type: 'lead_ingest', status: 'queued', priority: 2, payload: {}, created_at: null, updated_at: null },
    { id: 2, task_type: 'site_build', status: 'running', priority: 1, payload: {}, created_at: null, updated_at: null },
    { id: 3, task_type: 'follow_up', status: 'completed', priority: 4, payload: {}, created_at: null, updated_at: null },
    { id: 4, task_type: 'approval_gate', status: 'failed', priority: 1, payload: {}, created_at: null, updated_at: null },
  ]

  const lanes = groupTasksByStatus(tasks)

  assert.deepEqual(
    lanes.map((lane) => [lane.id, lane.count]),
    [
      ['queued', 1],
      ['running', 1],
      ['completed', 1],
      ['failed', 1],
    ],
  )
  assert.equal(lanes[0].tasks[0]?.task_type, 'lead_ingest')
  assert.equal(lanes[1].tasks[0]?.task_type, 'site_build')
})

test('buildAutopilotSnapshot surfaces operator pressure from tasks and approvals', () => {
  const tasks: TaskRecord[] = [
    { id: 11, task_type: 'email_draft', status: 'queued', priority: 3, payload: {}, created_at: null, updated_at: null },
    { id: 12, task_type: 'deployment', status: 'running', priority: 1, payload: {}, created_at: null, updated_at: null },
    { id: 13, task_type: 'budget_guard', status: 'failed', priority: 1, payload: {}, created_at: null, updated_at: null },
  ]
  const approvals: ApprovalRecord[] = [
    {
      approval_id: 'aaa-111',
      type: 'autonomy_transition',
      status: 'pending',
      requested_by: 'operator',
      details: { reason: 'Enable autonomous mode' },
      created_at: '2026-04-01T12:00:00Z',
      expires_at: '2026-04-01T12:30:00Z',
    },
    {
      approval_id: 'bbb-222',
      type: 'config_change',
      status: 'pending',
      requested_by: 'operator',
      details: { key: 'daily_email_cap', value: 300 },
      created_at: '2026-04-01T11:45:00Z',
      expires_at: '2026-04-01T18:00:00Z',
    },
  ]

  const snapshot = buildAutopilotSnapshot(tasks, approvals, {
    queued: 3,
    running: 1,
    completed: 8,
    failed: 1,
  }, Date.parse('2026-04-01T12:00:00Z'))

  assert.equal(snapshot.queueDepth, 13)
  assert.equal(snapshot.activeTasks, 1)
  assert.equal(snapshot.blockedTasks, 1)
  assert.equal(snapshot.pendingApprovals, 2)
  assert.equal(snapshot.criticalApprovals, 1)
  assert.equal(snapshot.focusLabel, 'Operator attention required')
  assert.equal(snapshot.lanes[3].tasks[0]?.task_type, 'budget_guard')
})
