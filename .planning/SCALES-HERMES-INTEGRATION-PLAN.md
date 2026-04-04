# Scales Hermes Integration Plan

Last updated: 2026-04-01
Status: Approved for execution planning

## Target Surface

All dashboard-facing work in this plan targets the active War Room frontend:

- `hermes/web/frontend/` — current Next.js War Room with the newer 21st.dev/shadcn-style component surface

This plan does **not** target the older server-rendered dashboard templates in:

- `hermes/web/templates/`
- `hermes/web/static/`

Those remain legacy/support surfaces unless explicitly needed for API compatibility.

## Decision

Use Scales as a UX and interaction donor, not as a runtime replacement.

Keep Objective Hertz / Perseus / Hermes as the system brain:

- multi-daemon architecture
- A2A communication
- governance and approvals
- Hermes operator messaging
- War Room backend and current frontend

Adopt from Scales where it improves product feel:

- desktop buddy behavior
- clip-based companion presentation
- compact approval/chat bubble UX
- autopilot control-room patterns
- remote/mobile operator ergonomics

## Adopt / Adapt / Ignore

### Adopt

- Buddy window architecture and docked overlay behavior
- Mascot clip player model
- Buddy notifications and compact action approval UX
- Autopilot dashboard information architecture

### Adapt

- Agents page
- Group chat
- Task board
- Settings and integrations UX
- Remote/mobile access ideas
- Swarm concepts

### Ignore For Now

- Discover/social feed
- Wrapped
- DLNA/casting
- Broad dashboard surfaces that do not improve Hermes or operator workflow

## Recommended Execution Order

### Phase 1: Hermes Companion Foundation

Goal:
Ship a Hermes desktop companion that lives outside the main dashboard window.

Scope:

- transparent always-on-top buddy window in the desktop shell
- Kirito presented first as clip-based animation, not VRM
- notification bubble layer
- handoff into the main War Room

Out of scope:

- true 3D avatar runtime
- duplex voice
- swarm
- mobile app

Success criteria:

- Hermes appears as a stable desktop presence
- Operator can open the main War Room from the companion
- Buddy can display proactive Hermes notifications

### Phase 2: Autopilot War Room

Goal:
Bring Scales-style control-room UX into the active War Room frontend.

Scope:

- control room
- execution board
- approval queue
- live logs
- identity/operator context panel

Implementation note:
Use existing Objective Hertz backend primitives, not Scales task JSON files or runner internals.

Success criteria:

- Operator can see what is running, blocked, waiting approval, and recently completed
- UI reflects real backend state from Hermes/Perseus/Titan/ClawdBot

### Phase 3: Hermes Voice

Goal:
Add voice interaction to Hermes.

Primary path:

- existing desktop speech/transcription lane
- ElevenLabs for output voice/persona

Taskmaster role:

- reference material for duplex voice interaction flow only
- not a shell/runtime replacement

Success criteria:

- operator can speak to Hermes
- Hermes responds in configured voice
- voice interactions tie back into shared Hermes context

### Phase 4: Hermes Remote

Goal:
Improve away-from-desk control.

Scope:

- stronger Telegram operator flow
- remote approvals
- alerts and briefings
- optional WhatsApp-style path only if it materially helps

Success criteria:

- Hermes is useful away from the desktop
- remote interactions are grounded in the same system state as the War Room

### Phase 5: Multi-Agent Product Layer

Goal:
Add higher-level collaborative UX after the core loop is stable.

Scope:

- group chat / internal debate UI
- agent cards and role dashboards
- swarm visibility and delegation UX

Success criteria:

- these surfaces clarify and accelerate the system
- they do not replace or obscure the existing daemon architecture

## Product Comparison Summary

Scales is stronger at:

- consumer-facing desktop UX
- dashboard packaging
- presence and polish

Objective Hertz is stronger at:

- actual multi-agent architecture
- governance and approvals
- Hermes operator communications
- daemon separation and extensibility

Working rule:

Steal Scales' skin and interaction patterns.
Keep Objective Hertz's skeleton and brain.

## Next Planning Step

Translate Phase 1 and Phase 2 into file-level implementation plans against:

- `desktop/`
- `desktop/src-tauri/`
- `hermes/web/frontend/`
- Hermes backend APIs that feed the companion and War Room
