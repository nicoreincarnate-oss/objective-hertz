import { NextRequest, NextResponse } from 'next/server'

const BACKEND = process.env.BACKEND_URL || 'http://localhost:8500'

export async function POST(request: NextRequest) {
  const token = request.nextUrl.searchParams.get('token')
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const body = await request.json()
    const question = typeof body?.question === 'string' ? body.question.trim() : ''
    if (!question) {
      return NextResponse.json({ error: 'Question cannot be empty.' }, { status: 400 })
    }

    const res = await fetch(`${BACKEND}/api/insights?token=${token}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      cache: 'no-store',
    })

    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'Invalid request' }, { status: 400 })
  }
}
