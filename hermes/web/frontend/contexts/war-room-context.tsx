"use client"

import React, { createContext, useContext, useMemo } from "react"
import useSWR from "swr"
import { useToken, authHeaders } from "@/hooks/use-token"
import { useWarRoomSocket, type SyncPayload, type ConnectionStatus } from "@/hooks/use-websocket"

// ── Types ───────────────────────────────────────────────────────────────

export type HealthStatus = "green" | "amber" | "red"

export interface WarRoomData {
  health: SyncPayload["health"] | null
  pipeline: Record<string, number>
  leads: SyncPayload["leads"]
  events: SyncPayload["events"]
  connectionStatus: ConnectionStatus
  lastSyncTime: Date | null
  healthStatus: HealthStatus
  isLoading: boolean
}

const defaultData: WarRoomData = {
  health: null,
  pipeline: {},
  leads: [],
  events: [],
  connectionStatus: "disconnected",
  lastSyncTime: null,
  healthStatus: "green",
  isLoading: true,
}

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
  const value = useMemo<WarRoomData>(() => {
    if (ws.data) {
      return {
        health: ws.data.health,
        pipeline: ws.data.pipeline,
        leads: ws.data.leads,
        events: ws.data.events,
        connectionStatus: ws.status,
        lastSyncTime: ws.lastSyncTime,
        healthStatus: computeHealthStatus(ws.data.health),
        isLoading: false,
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
      connectionStatus: ws.status === "disconnected" ? (swrHealth ? "polling" : "disconnected") : ws.status,
      lastSyncTime: swrHealth ? new Date() : null,
      healthStatus: computeHealthStatus(health),
      isLoading: !swrHealth && !ws.data,
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
