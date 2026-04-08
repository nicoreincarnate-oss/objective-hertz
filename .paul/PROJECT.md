# Perseus — Project Charter

## What
Multi-daemon autonomous business system. 8 daemons (Perseus scheduler, Titan revenue engine, Hermes alerts/Telegram/Jarvis, Clawdbot site builder, Conway agent economics, Deerflow research, Ruflo code fixing, Openjarvis orchestrator) running on a Mac Studio M4 Max 36GB.

## Mission
Operate as an autonomous business — lead discovery → outreach → site building → invoicing → support — with the operator in supervisory mode. Free operator time for CADAM (the active revenue business) per 2026-03-30 Fintel Forge shelving decision.

## Hardware (confirmed 2026-04-07)
- Mac Studio M4 Max
- 14C CPU (10P + 4E), 32-core GPU
- 36 GB unified memory
- 512 GB internal SSD (+ 2TB Thunderbolt NVMe required)
- 410 GB/s memory bandwidth
- Apple model A3143

## Active Mega-Plans
- LiteLLM Routing (Phases 40-44) — provider-agnostic gateway, kills Anthropic lock-in
- Phase 42.5 v2 — Local Tier Hardening (THIS PROJECT)

## Source of Truth
The Phase 42.5 v2 design is locked at:
`/Users/majovega/.claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory/project_local_tier_phase_42_5_v2.md`

That file is authoritative. PAUL plans in `.paul/phases/42-5-local-tier-hardening/` execute slices of it.

## Core Constraints
- Quality bar: 80-90% of Claude Opus 4.6 effective quality across coding, outreach, logic, heavy thinking
- Compound system: local + 4-layer verifier + cloud safety net (NOT pure-local)
- No scope sacrifice for security — all P0/P1 fixes mandatory
- No mistakes tolerated — verifier + escalation catches everything
- Perseus daemons run ON the Studio colocated with the LLM stack
- Cost ceiling: $350/mo production + $100/mo shadow
- **Quality > launch date** (locked 2026-04-07): Perseus does NOT launch until the full compound stack is validated. First customer interaction lands on the full Phase 42.5 build, not on a rushed cloud-only intermediate. Target 6-8 weeks total.

## Operator Preferences (locked decisions)
- Maximalist scope, no shrink-to-minimal
- **Quality > everything** — quality > cost, quality > launch speed, quality > convenience
- Perseus must be runnable autonomously after launch
- Free operator time for CADAM revenue work
- Perseus launches RIGHT the first time, not iteratively from a half-built cloud-only state
