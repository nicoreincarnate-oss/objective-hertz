"use client"

import { useCallback, useEffect, useRef, useState } from "react"

export interface SyncPayload {
  type: "sync"
  health: {
    status: string
    mode: string
    metrics: {
      emails_sent_today: number
      warm_leads: number
      sales_closed: number
      pending_approvals: number
      revenue_cleared: number
      revenue_pending: number
    }
  }
  pipeline: Record<string, number>
  leads: Array<{
    id: number
    business_name: string
    email: string
    industry: string
    status: string
    lead_score: number
    created_at: string | null
  }>
  events: Array<{
    id: number
    event_type: string
    payload: Record<string, unknown>
    created_at: string | null
    acknowledged: boolean
  }>
  // Extended sync fields (Phase 1 upgrade)
  task_queue?: Record<string, number>
  daemons?: Record<string, {
    status: string
    last_heartbeat: string | null
    paused: boolean
  }>
  budget?: {
    percent_used: number
    remaining: number
    total_spent: number
    exceeded: boolean
  }
}

export type ConnectionStatus = "connected" | "polling" | "disconnected"

interface UseWebSocketReturn {
  data: SyncPayload | null
  status: ConnectionStatus
  lastSyncTime: Date | null
}

const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000]

export function useWarRoomSocket(token: string | null): UseWebSocketReturn {
  const [data, setData] = useState<SyncPayload | null>(null)
  const [status, setStatus] = useState<ConnectionStatus>("disconnected")
  const [lastSyncTime, setLastSyncTime] = useState<Date | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttempt = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connect = useCallback(() => {
    if (!token) return

    // Determine WebSocket URL from current page location
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    // Connect directly to FastAPI backend (not through Next.js proxy)
    const backendHost = process.env.NEXT_PUBLIC_WS_URL || "localhost:8500"
    const wsUrl = `${proto}//${backendHost}/ws`

    try {
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        // Send auth token as first message
        ws.send(JSON.stringify({ token }))
        reconnectAttempt.current = 0
        setStatus("connected")
      }

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (payload.type === "sync") {
            setData(payload as SyncPayload)
            setLastSyncTime(new Date())
          }
          // Ignore pong and other message types
        } catch {
          // Malformed JSON — ignore
        }
      }

      ws.onclose = (event) => {
        wsRef.current = null
        if (event.code === 4001) {
          // Auth failure — don't reconnect
          setStatus("disconnected")
          return
        }
        setStatus("polling")
        scheduleReconnect()
      }

      ws.onerror = () => {
        // onclose will fire after this — handle reconnect there
      }
    } catch {
      setStatus("polling")
      scheduleReconnect()
    }
  }, [token])

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    const delay = RECONNECT_DELAYS[
      Math.min(reconnectAttempt.current, RECONNECT_DELAYS.length - 1)
    ]
    reconnectAttempt.current++
    reconnectTimer.current = setTimeout(connect, delay)
  }, [connect])

  useEffect(() => {
    connect()

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  // Send periodic pings to keep connection alive
  useEffect(() => {
    if (status !== "connected") return
    const interval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "ping" }))
      }
    }, 30000)
    return () => clearInterval(interval)
  }, [status])

  return { data, status, lastSyncTime }
}
