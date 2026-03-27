# THE WAR ROOM UPGRADE PLAN

## Tools Stack

| Tool | Role | How It's Used |
|---|---|---|
| **UI UX Pro Max** (Claude Code plugin, v2.5.0) | Design intelligence layer | Auto-activates on all UI work. Generates the master design system (color palette, typography, spacing, UX guidelines) BEFORE any component work begins. Every component decision flows through this system. |
| **21st.dev** | Component library | `npx shadcn@latest add "https://21st.dev/r/..."` pulls production-ready Shadcn/UI components directly into the Next.js frontend. |
| **Google Stitch 2.0** | Layout prototyping | Text-to-UI generation for prototyping new layouts and component arrangements before coding them. |
| **Nano Banana 2** | AI-generated visual assets | Gemini API generates custom hero graphics, agent avatars, pipeline icons, empty states, and branded textures. |

## Upgrade Architecture

```
UI UX Pro Max (Design System Generator)
    │
    ├── Analyzes: War Room project type (autonomous AI command center)
    ├── Generates: Color palette, typography, spacing, component rules
    ├── Outputs: Design tokens that ALL components below must follow
    │
    ├── 21st.dev Components ──→ Styled through Pro Max design system
    ├── Stitch 2.0 Prototypes ──→ Validated against Pro Max guidelines
    └── Nano Banana 2 Assets ──→ Color-matched to Pro Max palette
```

---

## Phase 0: DESIGN SYSTEM FOUNDATION (UI UX Pro Max)

**Run first. Everything else depends on this.**

Tell Claude Code: "I'm building an autonomous AI revenue system command center dashboard called the Perseus War Room. It's a Next.js 16 + React 19 + Shadcn/UI + Tailwind CSS 4 + Framer Motion dark-themed dashboard. The aesthetic is cyberpunk command center — dark backgrounds, gold/teal accents, glassmorphism panels, aurora effects. Generate a complete design system."

UI UX Pro Max will:
- Run 5 parallel searches across typography, color palettes, spacing, and UX patterns
- Generate a design system tailored to "autonomous AI command center" product type
- Output design tokens (CSS variables, Tailwind config extensions, component rules)
- Provide UX guidelines for dashboard information density, real-time data display, and operator workflows

**This design system becomes the source of truth for all decisions below.**

---

## Phase 1: REAL-TIME LAYER (Foundation — everything depends on this)

**What changes:**
- Replace 30-second SWR polling with WebSocket connections
- FastAPI WebSocket endpoint in `hermes/web/app.py`
- Client-side WebSocket hook in `hooks/use-websocket.ts`
- Every component below gets instant updates instead of stale data

**Backend (`hermes/web/app.py`):**
- Add `@app.websocket("/ws")` endpoint
- Authenticate via token in first message
- Broadcast events: pipeline changes, new leads, agent messages, health updates, budget changes
- Keep SWR as fallback for initial page load and reconnection

**Frontend (`hooks/use-websocket.ts`):**
- Custom hook: `useWarRoomSocket(token)`
- Auto-reconnect with exponential backoff
- Event type routing to component-specific state updates
- Connection status indicator in the UI

**UI UX Pro Max role:** Guides the connection status indicator design — where to place it, how to show connected/reconnecting/disconnected states, animation patterns for incoming data.

---

## Phase 2: HERO CARD → UI UX Pro Max + 21st.dev + Nano Banana 2

**Current state:** Static hero card with floating orbs, mode indicator, 4-column signal rail.

**Upgrade:**
- Pull [Aurora Background](https://21st.dev/aceternity/aurora-background) from 21st.dev
- Pull [Orb Effect](https://21st.dev/serafimcloud/orb-effect) from 21st.dev
- Replace static floating orbs with dynamic aurora that shifts color based on system health:
  - Green aurora = all systems healthy
  - Amber aurora = warnings present
  - Red aurora = incidents active
- UI UX Pro Max defines the exact color values, transition timing, and glow intensity for each state
- Nano Banana 2 generates a custom Perseus branded hero illustration — an AI god's-eye command center visual that sits behind the text

**21st.dev components:**
```bash
npx shadcn@latest add "https://21st.dev/r/aceternity/aurora-background"
```

---

## Phase 3: METRICS ROW → UI UX Pro Max + 21st.dev + Stitch 2.0

**Current state:** 4 metric cards with CountUp animation and shimmer overlay.

**Upgrade:**
- Pull [Interactive Flip Cards](https://github.com/Shadcn-Widgets/Interactive-Shadcn-Flip-Cards) — front shows the metric with trend arrow, hover/click flips to reveal a sparkline chart of the historical trend
- Expand from 4 cards to 6: add **Pipeline Velocity** (leads/day moving through stages) and **Budget Remaining** (Conway spend vs $800 cap)
- Use Stitch 2.0 to prototype the 6-card grid layout before coding
- Animated CountUp stays but gets easing upgrades (spring physics via Framer Motion)
- UI UX Pro Max defines card sizing, spacing, number formatting, trend indicator colors, and flip animation timing
- Real-time updates via WebSocket — metrics animate when they change, not on a 30-second cycle

---

## Phase 4: PIPELINE PULSE → UI UX Pro Max + 21st.dev + Stitch 2.0 (biggest visual upgrade)

**Current state:** 13-stage horizontal bar chart with animated bars and glow effect.

**Upgrade:**
- Replace flat bar charts with an **interactive 3D pipeline flow**
- Pull [Globe](https://21st.dev/magicui/globe/default) component as inspiration for a 3D orbital pipeline visualization — leads represented as particles orbiting through stages
- Each stage is a node; connections between stages show flow rate with animated particles
- **Interactive controls:**
  - Click any stage to expand and see the leads currently in that stage
  - Drag leads between stages for manual overrides (operator intervention)
  - Hover a stage to see conversion rate, average time in stage, and bottleneck warnings
- Stitch 2.0 prototypes the new layout with stage-to-stage animated transitions and conversion funnels
- UI UX Pro Max defines the 3D perspective, node sizing, particle speed, interaction patterns, and accessibility considerations
- Nano Banana 2 generates 13 unique stage icons (one per pipeline stage: discovered, researched, email_sent, replied, interested, demo_scheduled, demo_built, proposal_sent, negotiating, closed, invoiced, paid, churned)

---

## Phase 5: AGENT CHAT → UI UX Pro Max + 21st.dev + Nano Banana 2

**Current state:** Simple textarea with agent/priority dropdowns, 5-message thread display.

**Upgrade:**
- Pull [Expandable Chat](https://21st.dev/community/components/jakobhoeg/expandable-chat) from 21st.dev
- Pull [Chat Bubble](https://21st.dev/community/components/jakobhoeg/chat-bubble) from 21st.dev
- Rich chat interface with:
  - Typing indicators (agent is processing)
  - Message status (sent → delivered → acknowledged → completed)
  - Agent-specific styling (each daemon has its own color)
  - Inline action buttons in agent responses
- Nano Banana 2 generates unique avatar icons:
  - **Perseus** = strategist/chess piece aesthetic
  - **Titan** = engine/industrial aesthetic
  - **ClawdBot** = robot hands/mechanical aesthetic
  - **Hermes** = messenger/wings aesthetic
- UI UX Pro Max defines chat bubble sizing, message spacing, avatar placement, status indicator design, and mobile responsiveness
- WebSocket pushes agent responses in real-time instead of polling

---

## Phase 6: SIGNAL LEDGER → UI UX Pro Max + 21st.dev

**Current state:** Event timeline with 15+ color-coded event types, relative time, scroll area.

**Upgrade:**
- Real-time event stream via WebSocket push — events appear instantly
- Pull [Glow Effect](https://21st.dev/ibelick/glow-effect) from 21st.dev for event severity highlighting
- Events animate in from the right with Framer Motion `layoutAnimation`
- Critical events pulse with red glow, important events pulse gold
- **Filterable:** toggle by event type, daemon, and severity
- **Expandable:** click an event to see full payload details
- Sound notifications for critical events (optional, user-togglable)
- UI UX Pro Max defines event card styling, glow intensity per severity, animation timing, filter UX patterns, and information hierarchy

---

## Phase 7: LEADS TABLE → UI UX Pro Max + 21st.dev

**Current state:** Expandable rows, sortable by score/date, status badges, color-coded scores.

**Upgrade:**
- Pull [Data Table with TanStack Filters](https://21st.dev/community/components/originui/table/data-table-with-filters-made-with-tan-stack-table) from 21st.dev
- Pull [Sortable](https://21st.dev/diceui/sortable) from 21st.dev for drag-and-drop row reordering
- Full interactive table:
  - Multi-column sort (score, date, status, industry)
  - Filter by status, industry, score range
  - Search by business name or email
  - Expandable row detail panels with full lead history
  - Inline actions: approve, reject, escalate, send to stage
- Click a lead to open a **slide-over detail panel** with:
  - Full lead timeline (every stage transition with timestamps)
  - Email thread history
  - Site build status
  - Score breakdown
  - One-click actions
- UI UX Pro Max defines table density, row height, column priorities, filter UX, detail panel layout, and mobile table adaptations

---

## Phase 8: STRATEGIC VIEW → UI UX Pro Max + 21st.dev

**Current state:** Simple Q&A panel with text input, async request to /api/insights.

**Upgrade:**
- Upgrade to a full chat-style interface using the Expandable Chat component (shared with Agent Chat)
- **Streaming responses** — token-by-token display as Claude thinks (SSE from backend)
- **Suggested questions** based on current pipeline state:
  - "Why did close rate drop this week?"
  - "Which industry has the highest conversion?"
  - "What's the biggest bottleneck right now?"
- Response cards with evidence citations and action buttons
- UI UX Pro Max defines the streaming text animation, suggested question chip design, evidence card layout, and interaction flow

---

## Phase 9: CINEMATIC BACKDROP → UI UX Pro Max + 21st.dev + Nano Banana 2

**Current state:** Video background (`perseus-startup.mp4`) with gradient overlays and radial gold glow.

**Upgrade:**
- Pull [Background Gradient Animation](https://21st.dev/aceternity/background-gradient-animation/default) from 21st.dev
- Pull [Liquid Glass](https://21st.dev/community/components/s/liquid-glass) effects from 21st.dev
- All panels get **glassmorphism treatment** — backdrop blur with subtle glass reflections and frosted borders
- Nano Banana 2 generates a custom animated background texture that matches the Perseus dark theme with gold/teal accents
- The background subtly reacts to system state (calmer when idle, more active when pipeline is busy)
- UI UX Pro Max defines glassmorphism opacity, blur radius, border treatment, and ensures text remains readable at all contrast ratios (WCAG AA compliance)

---

## Phase 10: NANO BANANA 2 GENERATED ASSETS (visual layer)

**Custom AI-generated visuals for the entire War Room:**

| Asset | Description | Where It's Used |
|---|---|---|
| Hero banner | Command center aesthetic, dark with gold/teal energy streams | Hero Card background |
| Agent avatars (x4) | Perseus (strategist), Titan (engine), ClawdBot (robot), Hermes (messenger) | Agent Chat, Signal Ledger |
| Pipeline stage icons (x13) | Unique icon per stage matching War Room aesthetic | Pipeline Pulse nodes |
| Empty state illustrations (x4) | No leads, no events, system idle, search no results | Various empty states |
| Loading state artwork | Animated-ready loading visual | Page load, data fetching |
| Background texture | Tileable dark texture with subtle gold grid pattern | Cinematic Backdrop |

**Generation approach:**
- Use Gemini API (Nano Banana 2) via Google AI Studio
- Prompt each asset with the UI UX Pro Max design system colors and style guidelines
- Generate at 2x resolution, export as WebP for performance
- Store in `hermes/web/frontend/public/assets/generated/`

---

## Build Order (for Claude Code session)

```
Phase 0: UI UX Pro Max design system generation          [Session 1]
Phase 1: WebSocket real-time layer (backend + frontend)  [Session 1]
Phase 9: Cinematic backdrop + glassmorphism panels       [Session 2]
Phase 2: Hero Card aurora + health-reactive colors       [Session 2]
Phase 3: Metrics Row flip cards + 6-card grid            [Session 2]
Phase 4: Pipeline Pulse 3D interactive flow              [Session 3]
Phase 5: Agent Chat rich interface + avatars             [Session 3]
Phase 6: Signal Ledger real-time + glow effects          [Session 3]
Phase 7: Leads Table TanStack + slide-over detail        [Session 4]
Phase 8: Strategic View streaming + suggestions          [Session 4]
Phase 10: Nano Banana 2 asset generation                 [Session 4]
```

---

## Files That Will Be Modified

**Backend (Python):**
- `hermes/web/app.py` — WebSocket endpoint, SSE streaming for insights

**Frontend (TypeScript/React):**
- `app/page.tsx` — Layout restructure, WebSocket integration
- `app/globals.css` — Design system tokens, glassmorphism classes, new animations
- `hooks/use-websocket.ts` — NEW: WebSocket connection hook
- `hooks/use-token.ts` — Minor: pass token to WebSocket
- `components/hero-card.tsx` — Aurora background, health-reactive colors
- `components/metrics-row.tsx` — Flip cards, 6-card grid, spring animations
- `components/pipeline-pulse.tsx` — 3D pipeline flow, interactive stages, drag-and-drop
- `components/agent-chat.tsx` — Rich chat UI, typing indicators, avatars
- `components/signal-ledger.tsx` — Real-time stream, glow effects, filters
- `components/leads-table.tsx` — TanStack table, filters, slide-over detail panel
- `components/strategic-view.tsx` — Chat-style, streaming, suggested questions
- `components/cinematic-backdrop.tsx` — Gradient animation, liquid glass
- `components/count-up.tsx` — Spring easing upgrade
- `package.json` — New dependencies (TanStack, WebSocket client)
- `tailwind.config.ts` — Design system token extensions

**New files:**
- `hooks/use-websocket.ts`
- `components/lead-detail-panel.tsx`
- `components/pipeline-stage-detail.tsx`
- `components/connection-status.tsx`
- `public/assets/generated/` — All Nano Banana 2 outputs
