import { NextRequest, NextResponse } from 'next/server'

const BACKEND = process.env.BACKEND_URL || 'http://localhost:8500'

type BackendEvent = {
  id: string | number
  event_type: string
  payload?: Record<string, unknown>
  created_at?: string
  acknowledged?: boolean
}

function toStringValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

function normalizeChatEvents(events: BackendEvent[]) {
  const thread: Array<{
    id: string
    target_agent: string
    priority: string
    message: string
    created_at: string
    acknowledged: boolean
    response?: string
    response_at?: string
  }> = []

  for (const event of events) {
    const payload = event.payload ?? {}
    if (event.event_type === 'operator_message_sent') {
      thread.push({
        id: String(event.id),
        target_agent: toStringValue(payload.target_agent || 'agent'),
        priority: toStringValue(payload.priority || 'routine'),
        message: toStringValue(payload.message),
        created_at: toStringValue(event.created_at),
        acknowledged: false,
      })
      continue
    }

    if (event.event_type === 'agent_message_ack') {
      const agent = toStringValue(payload.agent || 'agent')
      const operatorMessage = toStringValue(payload.operator_message)
      const reply = toStringValue(payload.reply || 'Acknowledged.')
      const matched = [...thread].reverse().find(
        (item) => item.target_agent === agent && item.message === operatorMessage
      )

      if (matched) {
        matched.acknowledged = true
        matched.response = reply
        matched.response_at = toStringValue(event.created_at)
      } else {
        thread.push({
          id: `ack-${event.id}`,
          target_agent: agent,
          priority: toStringValue(payload.priority || 'routine'),
          message: operatorMessage || reply,
          created_at: toStringValue(event.created_at),
          acknowledged: true,
          response: reply,
          response_at: toStringValue(event.created_at),
        })
      }
    }
  }

  return thread
}

export async function GET(request: NextRequest) {
  const authHeader = request.headers.get('authorization') || ''
  if (!authHeader.toLowerCase().startsWith('bearer ')) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const res = await fetch(`${BACKEND}/api/events`, {
    headers: { Authorization: authHeader },
    cache: 'no-store',
  })
  const events = await res.json()
  const chatEvents = Array.isArray(events)
    ? normalizeChatEvents(
        events.filter((e: BackendEvent) =>
          e.event_type === 'operator_message_sent' || e.event_type === 'agent_message_ack'
        )
      )
    : []
  return NextResponse.json(chatEvents, { status: res.status })
}

export async function POST(request: NextRequest) {
  const authHeader = request.headers.get('authorization') || ''
  if (!authHeader.toLowerCase().startsWith('bearer ')) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  try {
    const body = await request.formData()
    const target_agent = body.get('target_agent') as string
    const priority = body.get('priority') as string
    const message = body.get('message') as string

    if (!target_agent || !message) {
      return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
    }

    const formData = new URLSearchParams()
    formData.set('target_agent', target_agent)
    formData.set('priority', priority || 'priority')
    formData.set('message', message)

    const res = await fetch(`${BACKEND}/api/operator-chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Authorization: authHeader,
      },
      body: formData.toString(),
      redirect: 'manual',
    })

    if (res.status === 303 || res.status === 200) {
      return NextResponse.json({ success: true })
    }

    return NextResponse.json({ error: 'Backend error' }, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'Invalid request' }, { status: 400 })
  }
}
