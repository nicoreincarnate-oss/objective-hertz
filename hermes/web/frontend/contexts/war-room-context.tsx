"use client"

import React, { createContext, useContext, useMemo, useRef, useCallback } from "react"
import useSWR from "swr"
import { useToken, authHeaders } from "@/hooks/use-token"
import { useWarRoomSocket, type SyncPayload, type ConnectionStatus } from "@/hooks/use-websocket"

// ── Types ───────────────────────────────────────────────────────────────

export type HealthStatus = "green" | "amber" | "red"

// Rolling history of metric values — each key stores last N data points
export type MetricHistory = Record<string, number[]>

export interface WarRoomData {
  health: SyncPayload["health"] | null
  pipeline: Record<string, number>
  leads: SyncPayload["leads"]
  events: SyncPayload["events"]
  taskQueue: Record<string, number>
  daemons: NonNullable<SyncPayload["daemons"]>
  budget: NonNullable<SyncPayload["budget"]>
  connectionStatus: ConnectionStatus
  lastSyncTime: Date | null
  healthStatus: HealthStatus
  isLoading: boolean
  metricHistory: MetricHistory
}

const defaultBudget = { percent_used: 0, remaining: 800, total_spent: 0, exceeded: false }

const defaultData: WarRoomData = {
  health: null,
  pipeline: {},
  leads: [],
  events: [],
  taskQueue: {},
  daemons: {},
  budget: defaultBudget,
  connectionStatus: "disconnected",
  lastSyncTime: null,
  healthStatus: "green",
  isLoading: true,
  metricHistory: {},
}

const MAX_HISTORY = 30 // Keep last 30 data points per metric

const WarRoomContext = createContext<WarRoomData>(defaultData)

// ── Helper: compute health status from metrics ──────────────────────────

function computeHealthStatus(health: SyncPayload["health"] | null): HealthStatus {
  if (!health) return "amber"
  const m = health.metrics
  // Red: pending approvals > 5, or revenue cleared is 0 with sales > 0
  if (m.pending_approvals > 5) return "red"
  // Amber: any pending approvals, or no emails sent today
  if (m.pending_approvals > 0 || m.emails_sent_today === 0) return "amber"
  return "green"
}

// ── SWR fallback fetcher ────────────────────────────────────────────────

function createFetcher(token: string) {
  return (url: string) =>
    fetch(url, { headers: authHeaders(token), cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
}

// ── Provider ────────────────────────────────────────────────────────────

export function WarRoomProvider({ children }: { children: React.ReactNode }) {
  const { token } = useToken()
  const historyRef = useRef<MetricHistory>({})

  // Append current metrics to rolling history on each sync
  const appendHistory = useCallback((metrics: SyncPayload["health"]["metrics"] | null) => {
    if (!metrics) return
    const h = historyRef.current
    const entries: Record<string, number> = {
      revenue_cleared: metrics.revenue_cleared,
      revenue_pending: metrics.revenue_pending,
      emails_sent_today: metrics.emails_sent_today,
      warm_leads: metrics.warm_leads,
      sales_closed: metrics.sales_closed,
      pending_approvals: metrics.pending_approvals,
    }
    for (const [key, val] of Object.entries(entries)) {
      if (!h[key]) h[key] = []
      h[key].push(val)
      if (h[key].length > MAX_HISTORY) h[key] = h[key].slice(-MAX_HISTORY)
    }
  }, [])

  // Primary: WebSocket real-time data
  const ws = useWarRoomSocket(token)

  // Fallback: SWR polling (only active when WebSocket is not connected)
  const swrEnabled = ws.status !== "connected" && !!token
  const fetcher = useMemo(() => (token ? createFetcher(token) : null), [token])

  const { data: swrHealth } = useSWR(
    swrEnabled && fetcher ? "/api/health" : null,
    fetcher!,
    { refreshInterval: 30000 }
  )
  const { data: swrPipeline } = useSWR(
    swrEnabled && fetcher ? "/api/pipeline" : null,
    fetcher!,
    { refreshInterval: 30000 }
  )
  const { data: swrLeads } = useSWR(
    swrEnabled && fetcher ? "/api/leads" : null,
    fetcher!,
    { refreshInterval: 30000 }
  )
  const { data: swrEvents } = useSWR(
    swrEnabled && fetcher ? "/api/events" : null,
    fetcher!,
    { refreshInterval: 30000 }
  )

  // Merge: prefer WebSocket data, fall back to SWR
  // Accumulate history on each WebSocket sync
  if (ws.data?.health?.metrics) {
    appendHistory(ws.data.health.metrics)
  }

  const value = useMemo<WarRoomData>(() => {
    if (ws.data) {
      return {
        health: ws.data.health,
        pipeline: ws.data.pipeline,
        leads: ws.data.leads,
        events: ws.data.events,
        taskQueue: ws.data.task_queue || {},
        daemons: ws.data.daemons || {},
        budget: ws.data.budget || defaultBudget,
        connectionStatus: ws.status,
        lastSyncTime: ws.lastSyncTime,
        healthStatus: computeHealthStatus(ws.data.health),
        isLoading: false,
        metricHistory: { ...historyRef.current },
      }
    }

    // SWR fallback
    const health = swrHealth
      ? {
          status: swrHealth.status || "unknown",
          mode: swrHealth.mode || "review",
          metrics: swrHealth.metrics || {
            emails_sent_today: 0,
            warm_leads: 0,
            sales_closed: 0,
            pending_approvals: 0,
            revenue_cleared: 0,
            revenue_pending: 0,
          },
        }
      : null

    return {
      health,
      pipeline: swrPipeline || {},
      leads: swrLeads || [],
      events: swrEvents || [],
      taskQueue: {},
      daemons: {},
      budget: defaultBudget,
      connectionStatus: ws.status === "disconnected" ? (swrHealth ? "polling" : "disconnected") : ws.status,
      lastSyncTime: swrHealth ? new Date() : null,
      healthStatus: computeHealthStatus(health),
      isLoading: !swrHealth && !ws.data,
      metricHistory: { ...historyRef.current },
    }
  }, [ws.data, ws.status, ws.lastSyncTime, swrHealth, swrPipeline, swrLeads, swrEvents])

  return (
    <WarRoomContext.Provider value={value}>
      {children}
    </WarRoomContext.Provider>
  )
}

// ── Hook ────────────────────────────────────────────────────────────────

export function useWarRoom(): WarRoomData {
  return useContext(WarRoomContext)
}
