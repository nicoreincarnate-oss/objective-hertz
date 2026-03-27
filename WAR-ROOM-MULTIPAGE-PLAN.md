# War Room Multi-Page Architecture Plan

## Overview

Restructure the War Room from a single scrolling page into **5 themed pages** connected by a persistent navigation shell. Each page gets dedicated 21st.dev components, Google Stitch-generated layouts, and Nano Banana 2 imagery. UI UX Pro Max governs the design system across all pages.

## Current State → Target State

```
CURRENT (single page):                    TARGET (5 pages):
┌──────────────────────┐                  ┌──────────────────────┐
│ Hero Card            │                  │ /login    — Entrance │
│ Metrics Row          │                  │ /         — Command  │
│ Agent Chat | Pipeline│    ──────→       │ /pipeline — Pipeline │
│ Strategic View       │                  │ /agents   — Agents   │
│ Signal Ledger        │                  │ /intel    — Intel    │
│ Leads Table          │                  └──────────────────────┘
└──────────────────────┘                  + Persistent nav shell
```

## Tech Stack

- **Next.js 16 App Router** — file-based routing (`app/`, `app/pipeline/`, etc.)
- **21st.dev components** — 18 community components (listed below)
- **Google Stitch 2.0** — layout generation, empty states, MAGMA panels
- **Nano Banana 2** — AI-generated hero illustrations, agent avatars, stage icons
- **UI UX Pro Max** — design system governance, color palette, font pairing
- **Framer Motion** — page transitions, shared layout animations
- **WarRoomProvider** — shared context wraps all pages via layout.tsx

---

## Page 1: COMMAND CENTER (`/`)

**Theme**: Operational HUD — quick status, critical metrics, live alerts

### Component Map

| Section | Current | 21st.dev Replacement | Notes |
|---------|---------|---------------------|-------|
| Mode Badge | DIY pill | **HUD Status** (`isaiahbjork/hud-status-1`) | Trapezoidal SVG, typewriter text, variant maps to health state |
| Metric Cards | glass-card + CountUp | **Spotlight Card** (`easemize/spotlight-card`) | Mouse-tracking glow, `glowColor` per metric type |
| Metric Values | CountUp (mount only) | **Special Text** (`tom_ui/special-text`) | Scramble-reveal on value changes |
| Alert Ticker | (none) | **Text Marquee** (`tom_ui/text-marquee`) | Horizontal ticker of latest 5 critical events |
| CTA Buttons | basic Button | **Rainbow Borders Button** (`muhammad-binsalman/rainbow-borders-button`) | Animated rainbow border for Approve/Deploy/Send |
| Backdrop | BackgroundGradientAnimation + DotPattern | Keep as-is | Already upgraded |
| Signal Ledger | AnimatedList | Keep as-is | Already upgraded |

### Google Stitch Tasks
- Generate metric card inner layouts with sparkline + delta indicator
- Generate the alert ticker container with severity-based color coding

### What Moved Out
- Agent Chat → `/agents`
- Strategic View → `/intel`
- Pipeline Pulse → `/pipeline`
- Leads Table → `/pipeline`

---

## Page 2: PIPELINE (`/pipeline`)

**Theme**: Revenue funnel — manage leads, track progression, take action

### Component Map

| Section | Current | 21st.dev Replacement | Notes |
|---------|---------|---------------------|-------|
| Pipeline Overview | Flat vertical list | **Agent Plan** (`isaiahbjork/agent-plan`) | 13 stages as collapsible parent tasks, leads as subtasks |
| Pipeline Hero | (none) | **Stacked Panels** (`shadway/stacked-panels-cursor-interactive`) | 13 stage panels in 3D depth, cursor-interactive |
| Page Divider | (none) | **Lightning Split** (`cinquinandy/lightning-split`) | Electric bolt splits pipeline (left) from lead detail (right) |
| Leads Table | TanStack table | Moves here, enhanced with row selection | Right pane of Lightning Split |
| Lead Detail | Sheet slide-over | Full right pane | More space, better UX |

### Google Stitch Tasks
- Generate pipeline stage transition animations (lead moving between stages)
- Generate the Lightning Split left/right content wrappers
- Generate lead action confirmation modals

### Nano Banana 2 Tasks
- Stage icons: 13 custom icons for each pipeline stage (Discovered, Researched, Drafted, etc.)
- Empty state illustrations for "No leads at this stage"

---

## Page 3: AGENTS (`/agents`)

**Theme**: Daemon control — communicate with agents, monitor tasks, review health

### Component Map

| Section | Current | 21st.dev Replacement | Notes |
|---------|---------|---------------------|-------|
| Agent Chat | Basic chat panel | **Message Dock** (`isaiahbjork/message-dock`) | Bottom pill dock, 4 agents, gradient themes per agent |
| Lead Geography | (none — net new) | **COBE Globe** (`reapollo/cobe-globe`) | 3D rotating globe with lead markers and arcs |
| Agent Dashboards | Signal rail pills | **Vertical Tabs** (`0xUrvish/vertical-tabs`) | Left sidebar: Perseus, Titan, Hermes, ClawdBot, Conway |

### Google Stitch Tasks
- Generate agent status dashboard cards (task count, health bar, recent actions, uptime)
- Generate the Vertical Tabs content panels for each daemon
- Generate agent task queue table with priority sorting

### Nano Banana 2 Tasks
- Custom agent avatars: Perseus (strategist), Titan (engine), Hermes (messenger), ClawdBot (builder), Conway (economist)
- Agent background illustrations for each tab

---

## Page 4: INTELLIGENCE (`/intel`)

**Theme**: Strategic brain — AI chat, MAGMA insights, pattern analysis

### Component Map

| Section | Current | 21st.dev Replacement | Notes |
|---------|---------|---------------------|-------|
| Page Hero | (none) | **Hero Futuristic** (`reapollo/hero-futuristic`) | WebGPU scanning effect, "God's-Eye View" headline |
| AI Loading | Loader2 spinner | **Spiral Animation** (`Kain0127/spiral-animation`) | Particle spiral during AI streaming |
| Insight Cards | Plain text responses | **Spotlight Card** (`easemize/spotlight-card`) | Mouse-tracking glow on each recommendation card |
| Strategic Chat | Strategic View | Enhanced with Hero Futuristic entrance | SSE streaming preserved |

### Google Stitch Tasks
- Generate MAGMA insights dashboard (action-to-outcome graphs, confidence scores, titan_rules)
- Generate the insight card layout with recommended actions as interactive checklist
- Generate a "conversation history" sidebar for past strategic questions

### Nano Banana 2 Tasks
- Hero illustration: brain/neural network visualization
- Empty state: "Ask a question to begin analysis"

---

## Page 5: LOGIN / ENTRANCE (`/login`)

**Theme**: Cinematic entrance — first impression, authentication

### Component Map

| Section | Current | 21st.dev Replacement | Notes |
|---------|---------|---------------------|-------|
| Page Hero | Basic glass card | **Cinematic Landing Hero** (`easemize/cinematic-landing-hero`) | Scroll-triggered animation, iPhone mockup → dashboard preview |
| Auth Form | TokenInput | **Login-1** (`abishek1512/login-1`) | Interactive gradient bg, glowing input borders, sweep button |

### Google Stitch Tasks
- Generate the transition animation from login to Command Center
- Generate the "system initializing" sequence after token submission

### Nano Banana 2 Tasks
- PERSEUS logo hero illustration
- Background textures for the cinematic hero

---

## PERSISTENT NAVIGATION SHELL

Wraps all pages via `app/layout.tsx`. Provides consistent navigation and system-wide alerts.

### Component Map

| Element | 21st.dev Component | Notes |
|---------|-------------------|-------|
| Nav Menu | **Menu** (`coss.com/menu`) | Dropdown from PERSEUS badge, keyboard shortcuts |
| Alert Ticker | **Text Marquee** (`tom_ui/text-marquee`) | Persistent top bar with critical system alerts |
| Design System | **Reuno Hero** (`reuno-ui/hero`) | Extract mesh gradient shader + PulsingBorder for active nav |

### Keyboard Shortcuts
- `Cmd+1` → Command Center
- `Cmd+2` → Pipeline
- `Cmd+3` → Agents
- `Cmd+4` → Intelligence
- `Cmd+K` → Command palette (quick search/action)

---

## Implementation Order

### Phase 0: Foundation (do first)
1. Create Next.js App Router page structure (`app/pipeline/page.tsx`, etc.)
2. Extract WarRoomProvider to shared layout
3. Build persistent nav shell with Menu component
4. Set up page transitions with Framer Motion `layoutId`

### Phase 1: Command Center Refinement
1. Install HUD Status, Spotlight Card, Special Text, Text Marquee, Rainbow Borders Button
2. Replace mode pill with HUD Status
3. Wrap metric cards in Spotlight Cards
4. Add Special Text for value animations
5. Add Text Marquee alert ticker
6. Google Stitch: metric card inner layouts

### Phase 2: Pipeline Page (highest business value)
1. Install Agent Plan, Stacked Panels, Lightning Split
2. Build pipeline route with Agent Plan as primary view
3. Move Leads Table to right pane of Lightning Split
4. Google Stitch: stage transition animations
5. Nano Banana 2: 13 pipeline stage icons

### Phase 3: Agents Page
1. Install Message Dock, COBE Globe, Vertical Tabs
2. Build agents route with Vertical Tabs sidebar
3. Integrate Message Dock as floating bottom element
4. Add COBE Globe with lead geography data
5. Google Stitch: agent status dashboard cards
6. Nano Banana 2: custom agent avatars

### Phase 4: Intelligence Page
1. Install Hero Futuristic, Spiral Animation
2. Build intel route with Hero Futuristic entrance
3. Move Strategic View chat here
4. Add Spiral Animation as AI loading state
5. Google Stitch: MAGMA insights dashboard (net new)
6. Nano Banana 2: brain/neural network hero illustration

### Phase 5: Login / Entrance
1. Install Cinematic Landing Hero, Login-1
2. Replace TokenInput with Login-1
3. Add Cinematic Landing Hero as entrance sequence
4. Google Stitch: login → dashboard transition

### Phase 6: Polish
1. UI UX Pro Max: audit all pages for design consistency
2. Page transition animations between routes
3. Responsive breakpoints for all new components
4. Performance audit (bundle size, lazy loading heavy components)
5. Accessibility pass (keyboard nav, screen reader, reduced motion)

---

## 21st.dev Component Registry (18 total)

| # | Component | Author | Used On | Purpose |
|---|-----------|--------|---------|---------|
| 1 | Agent Plan | isaiahbjork | Pipeline | Hierarchical pipeline stage viewer |
| 2 | Message Dock | isaiahbjork | Agents | Floating agent chat dock |
| 3 | COBE Globe | reapollo | Agents | 3D lead geography globe |
| 4 | Menu | coss.com | Nav Shell | Dropdown navigation |
| 5 | Stacked Panels | shadway | Pipeline | 3D cursor-interactive stage overview |
| 6 | Cinematic Landing Hero | easemize | Login | Scroll-triggered entrance animation |
| 7 | Vertical Tabs | 0xUrvish | Agents | Agent dashboard sidebar |
| 8 | Special Text | tom_ui | Command | Scramble-reveal metric values |
| 9 | Text Marquee | tom_ui | Nav Shell + Command | Alert ticker |
| 10 | Hero Futuristic | reapollo | Intelligence | WebGPU hero with scanning effect |
| 11 | Spiral Animation | Kain0127 | Intelligence | AI loading particle spiral |
| 12 | Spotlight Card | easemize | Command + Intel | Mouse-tracking glow cards |
| 13 | Rainbow Borders Button | muhammad-binsalman | Command | Animated CTA buttons |
| 14 | Reuno Hero | reuno-ui | Nav Shell | Mesh gradient shader extraction |
| 15 | Login-1 | abishek1512 | Login | Interactive auth form |
| 16 | HUD Status | isaiahbjork | Command | Trapezoidal status badge |
| 17 | Lightning Split | cinquinandy | Pipeline | Electric bolt page divider |
| 18 | Cinematic Backdrop | (already installed) | All pages | Animated gradient background |

## Google Stitch 2.0 Usage Plan

| Page | Stitch Task | Input Description |
|------|------------|-------------------|
| Command | Metric card inner layout | "6 KPI cards with sparkline charts, delta arrows, dark glass theme, cyan accents" |
| Command | Alert ticker container | "Horizontal scrolling alert bar with severity color coding, dark theme" |
| Pipeline | Stage transition animation | "Animated card moving between pipeline stages with glow trail, dark theme" |
| Pipeline | Lead action modals | "Confirmation dialog for approve/reject/escalate actions, glass morphism" |
| Agents | Agent status dashboard cards | "4 daemon monitoring cards with task count, health bar, recent activity, dark HUD" |
| Agents | Agent task queue table | "Sortable task queue with priority indicators, dark theme, cyan highlights" |
| Intel | MAGMA insights dashboard | "AI learning engine panel with action-outcome graphs, confidence scores, dark analytical" |
| Intel | Conversation history sidebar | "Chat history list with timestamps, dark theme, search filter" |
| Login | Login-to-dashboard transition | "Cinematic zoom transition from auth form to dashboard, dark theme" |
| Login | System initializing sequence | "Boot sequence animation with progress bar, system check list, HUD aesthetic" |

---

*Last updated: 2026-03-26*
*Tools: 21st.dev (18 components) + Google Stitch 2.0 + Nano Banana 2 + UI UX Pro Max*
