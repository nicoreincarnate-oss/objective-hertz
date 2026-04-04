import test from 'node:test'
import assert from 'node:assert/strict'

import { deriveKiritoPresence, type KiritoSignal } from './kirito-presence.ts'

test('deriveKiritoPresence escalates when approvals or failures are piling up', () => {
  const presence = deriveKiritoPresence({
    mode: 'review',
    pendingApprovals: 4,
    blockedTasks: 2,
    activeTasks: 1,
    liveDaemons: 2,
    totalDaemons: 4,
    highSignalEvents: [
      { event_type: 'approval_required', payload: { reason: 'autonomy transition' } },
    ],
  })

  assert.equal(presence.mood, 'alert')
  assert.equal(presence.badgeLabel, 'Operator Link Required')
  assert.match(presence.statusLine, /approvals/i)
  assert.match(presence.guidanceLine, /clear the approval queue/i)
})

test('deriveKiritoPresence settles into focused execution when the fleet is healthy', () => {
  const signals: KiritoSignal = {
    mode: 'autonomous',
    pendingApprovals: 0,
    blockedTasks: 0,
    activeTasks: 3,
    liveDaemons: 4,
    totalDaemons: 4,
    highSignalEvents: [],
  }

  const presence = deriveKiritoPresence(signals)

  assert.equal(presence.mood, 'focused')
  assert.equal(presence.badgeLabel, 'Autonomous Sweep')
  assert.match(presence.statusLine, /3 active tasks/i)
  assert.match(presence.guidanceLine, /monitor/i)
})
