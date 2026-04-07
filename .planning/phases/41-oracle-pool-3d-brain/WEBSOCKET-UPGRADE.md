# Phase 41 Sub-phase 6 — WebSocket Upgrade (Future Work Scaffold)

> **Status:** NOT FOR EXECUTION. This is a complete implementation spec that future-you (or a future agent) can pick up when the polling approach becomes the bottleneck.
>
> **Owner:** Phase 41 (Oracle Pool 3D Brain)
> **Trigger to execute:** Operator inserts a memory and waits >2 seconds to see it appear, OR memory creation rate exceeds 1/min sustained.

---

## 1. Current State (Polling)

The 3D brain graph currently uses HTTP polling to refresh its view of `memory_provenance`.

- **File:** `hermes/web/frontend/components/olympus/Memory3DGraph.tsx`
- **Mechanism:** `setInterval(fetchGraph, 5000)` with a `document.hidden` skip so background tabs don't poll
- **Endpoint:** `GET /api/memory/graph?depth=3&limit=500`
- **Latency:** Up to 5 seconds for a newly committed memory to appear in the brain
- **Bandwidth:** ~50–200 KB per poll depending on graph size (full snapshot every tick)
- **CPU:** Minimal — JSON parse + force-graph reflow on each fetch
- **Failure mode:** If the API errors, the graph silently stops updating until the next tick recovers

This is "good enough" for the current scale (≤500 memories, single operator) but it has obvious limits: stale data, wasted bandwidth, and a coarse 5s heartbeat that feels laggy when the operator is actively poking the system.

---

## 2. Target State (WebSocket Subscription)

Replace polling with a push-based WebSocket subscription that streams memory deltas as they happen.

- **File:** Same — `hermes/web/frontend/components/olympus/Memory3DGraph.tsx`
- **Mechanism:** WebSocket subscription, no `setInterval`
- **Endpoint:** Existing `ws://host/ws` from `hermes/web/app.py` (already wired with auth handshake)
- **Auth:** First message must include the dashboard secret token. See `app.py:777-794` for the existing handshake pattern.
- **Latency:** Sub-100ms — visible to the operator as "instant"
- **Bandwidth:** Only diff payloads (one node + its edges), typically <2 KB per event
- **CPU:** Trivially low — incremental graph mutation instead of full reload
- **Failure mode:** Reconnect with exponential backoff; fall back to polling if WS keeps failing

---

## 3. Backend Changes Needed

The existing `/ws` endpoint pushes a "full sync" payload every 5 seconds, which is the wrong shape for the brain graph. We want **change notifications**, not heartbeats.

### Plan

1. **Add a Postgres `LISTEN/NOTIFY` trigger** on `memory_provenance` insert. The trigger fires `NOTIFY memory_added, '<json payload>'` whenever a row is inserted.
2. **Create `hermes/web/memory_ws.py`** — a small async module that:
   - Opens a dedicated psycopg connection in `LISTEN memory_added` mode
   - Awaits notifications in a background task spawned by app startup
   - On each notification, builds the broadcast envelope `{ type: 'memory_added', node: {...}, edges: [...] }`
   - Iterates `_ws_connections` (the existing set in `app.py`) and sends the payload to every connected client
3. **Hook into existing `_ws_connections`** — do NOT create a parallel connection set. Reuse the one in `app.py` so the existing auth, cleanup, and lifecycle code keeps working.
4. **Add event types** beyond `memory_added`:
   - `memory_removed` — payload `{ id }`
   - `memory_updated` — payload `{ id, version, node }` for version bumps
5. **Batching** — coalesce events that arrive within a 100ms window into a single frame to avoid frame-storm under bursty inserts.

### Files touched

| File | Change |
|---|---|
| `hermes/web/memory_ws.py` | NEW — listener + broadcaster |
| `hermes/web/app.py` | Wire `memory_ws.start()` into FastAPI lifespan; reuse `_ws_connections` |
| `scripts/migrations/NNN-memory-provenance-notify.sql` | NEW — `CREATE TRIGGER` + `CREATE FUNCTION` for `pg_notify` |
| `tests/test_memory_ws.py` | NEW — pytest-asyncio coverage |

---

## 4. Frontend Changes Needed

In `Memory3DGraph.tsx`:

1. **Replace** the polling `useEffect` with a WebSocket connection effect.
2. **On mount:** Open the WebSocket and immediately send `{ token: process.env.NEXT_PUBLIC_GRAPH_READONLY_TOKEN }` for auth (see §5 for why this is a separate token).
3. **On message:** Parse `{ type, node, edges }` and append to existing graph state via `ingestGraphData()`. That helper already handles new-node detection and dedupe, so the merge is one-line.
4. **On close:** Reconnect with exponential backoff — `1s, 2s, 4s, 8s, 16s` capped at 16s. Reset the backoff on a successful message.
5. **On error:** After 3 consecutive failed reconnect attempts, fall back to polling mode for resilience. This keeps the brain functional even if the WebSocket layer is broken in production.
6. **Cleanup on unmount:** Close the WebSocket and clear any reconnect timers.

### Pseudo-code

```tsx
useEffect(() => {
  let ws: WebSocket | null = null;
  let backoff = 1000;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;

  const connect = () => {
    ws = new WebSocket(`${wsBase}/ws`);
    ws.onopen = () => {
      backoff = 1000;
      failures = 0;
      ws?.send(JSON.stringify({ token: process.env.NEXT_PUBLIC_GRAPH_READONLY_TOKEN }));
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'memory_added') ingestGraphData({ nodes: [msg.node], edges: msg.edges });
      // ...handle removed/updated
    };
    ws.onclose = () => {
      failures += 1;
      if (failures >= 3) { startPollingFallback(); return; }
      reconnectTimer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 16000);
    };
    ws.onerror = () => ws?.close();
  };

  connect();
  return () => { ws?.close(); if (reconnectTimer) clearTimeout(reconnectTimer); };
}, []);
```

---

## 5. Auth Concerns

The existing `DASHBOARD_SECRET` is the master token for the war room. Exposing it to the browser via `NEXT_PUBLIC_*` would broaden its blast radius — anyone with view access to the frontend would have full dashboard write capability.

**Mitigation:** Introduce a separate read-only token specifically for graph subscription.

- Add `GRAPH_READONLY_TOKEN` to `.env`
- Validate it in the WebSocket handler in `app.py` — accept either `DASHBOARD_SECRET` (full access) or `GRAPH_READONLY_TOKEN` (subscribe-only, no command channel)
- Expose the read-only token to the frontend as `NEXT_PUBLIC_GRAPH_READONLY_TOKEN`
- Document the trade-off in `HANDOFF.md`: faster latency vs slightly looser secret handling, with a token scoped to read-only graph events

This keeps the master secret server-side and gives us a token we can rotate independently if it leaks.

---

## 6. Implementation Phases

### Phase A — Backend (4 hours)

- [ ] Create `hermes/web/memory_ws.py`
- [ ] Write the Postgres `LISTEN/NOTIFY` migration
- [ ] Wire the listener into FastAPI lifespan
- [ ] Reuse `_ws_connections` for broadcast
- [ ] Add `memory_added`, `memory_removed`, `memory_updated` event types
- [ ] Implement 100ms batching window
- [ ] Add `pytest-asyncio` tests covering: notify trigger, broadcast fan-out, connection cleanup, batching window

### Phase B — Frontend (2 hours)

- [ ] Replace polling `useEffect` with WebSocket effect in `Memory3DGraph.tsx`
- [ ] Add exponential backoff reconnection (1s → 16s cap)
- [ ] Add polling fallback after 3 consecutive failures
- [ ] Wire `ingestGraphData()` for incremental merge
- [ ] Chaos test: kill backend mid-session, verify fallback engages, restore backend, verify auto-reconnect

### Phase C — Verification (1 hour)

- [ ] Insert a memory via `psql` directly. Verify it appears in the browser within 200ms.
- [ ] Kill the Hermes daemon. Verify the frontend gracefully falls back to polling and remains functional.
- [ ] Restart the daemon. Verify the WebSocket auto-reconnects and live updates resume.
- [ ] Sustained-load test: insert 100 memories over 10 seconds, verify all 100 land in the graph with no drops.

**Total estimated effort:** 7 engineering hours.

---

## 7. Risks & Open Questions

- **Connection scaling.** The WebSocket loop in `app.py` does a full broadcast on every tick. Fine for <50 clients, but at higher scale we'd need to switch to a pub/sub fan-out (e.g., a single producer task with per-client queues). For Phase 41 use, single-operator, this is non-issue.
- **Frame storms.** Bursty memory inserts could overwhelm the client. **Resolution:** batch events within a 100ms window into a single WS frame. Already in the plan.
- **Deletions.** How do we handle memory deletions? **Resolution:** add a `memory_removed` event type that emits `{ id }` and let the frontend remove the node + dangling edges.
- **Updates.** How do we handle version bumps (memory edited, version incremented)? **Resolution:** add a `memory_updated` event with the new version payload; frontend replaces the node in place.
- **Initial state on connect.** Should the WebSocket send a full snapshot on first connect, or assume the client already has one from the initial HTTP load? **Recommendation:** initial HTTP load via the existing `/api/memory/graph` endpoint, then switch to WS for deltas. Keeps the WS protocol minimal.
- **Token rotation.** If `GRAPH_READONLY_TOKEN` leaks, how do we rotate without breaking active sessions? **Open question** — likely accept dual tokens during a rotation window.

---

## 8. Decision: When to Upgrade

| Version | Approach | Good for | Limit |
|---|---|---|---|
| **v1 (Phase 41 current)** | Polling, 5s tick | Simpler, ≤500 memories, single operator | Stale data, wasted bandwidth |
| **v2 (Phase 42+)** | WebSocket subscription | Live updates, efficient bandwidth | Slightly more complex auth surface |

**Trigger to upgrade:** If the operator inserts a memory and waits more than 2 seconds to see it appear in the brain, that's the cue. Equivalently, if memory creation rate exceeds 1/min sustained, polling becomes wasteful and the upgrade is justified.

---

## 9. Estimated Cost

- **Engineering:** ~7 hours total (4h backend + 2h frontend + 1h verification)
- **New packages:** None — uses existing FastAPI WebSocket support and the browser-native WebSocket API
- **New infrastructure:** None — Postgres `LISTEN/NOTIFY` is built in and we already have a connection pool
- **Operational risk:** Low — polling fallback ensures graceful degradation if WebSocket layer breaks

---

## 10. References

- **FastAPI WebSocket docs:** https://fastapi.tiangolo.com/advanced/websockets/
- **Postgres LISTEN/NOTIFY:** https://www.postgresql.org/docs/current/sql-notify.html
- **Existing WebSocket setup:** `hermes/web/app.py:625-823` (auth handshake at `app.py:777-794`)
- **Current polling implementation:** `hermes/web/frontend/components/olympus/Memory3DGraph.tsx`
- **Graph endpoint reused for initial load:** `GET /api/memory/graph?depth=3&limit=500`
