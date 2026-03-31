# Paperclip Codebase Teardown — What to Steal for Objective Hertz

**Source:** github.com/paperclipai/paperclip (MIT license — everything is fair game)
**Date:** 2026-03-30
**Agents used:** 3 parallel analysts (server core, UI/adapters, docs/config)
**Total patterns found:** 27 across all agents

---

## Priority 1: Immediate Steals (Trivial Effort, High Impact)

### 1. Agent State Machine with Pause Reasons
**Source:** `packages/shared/src/constants.ts` lines 13-21
**What:** 7 agent states: active, idle, running, paused, error, pending_approval, terminated. Pause reasons: manual, budget, system.
**Port to OH:** Add status enum + pause_reason to daemon health tracking. Currently our daemons are binary running/not-running.
**Effort:** 1-2 hours. Add columns to agent registry, update health endpoint.

### 2. Atomic Task Checkout
**Source:** Issues checkout via `checkoutRunId` + `executionLockedAt` with 409 conflict
**What:** Before working a task, agent must atomically claim it. Prevents two agents working the same thing.
**Port to OH:** Add `claimed_by` and `claimed_at` to task_queue with `WHERE claimed_by IS NULL` in claim query.
**Effort:** 1 hour. Already partially exists in our `claim_task()` but lacks 409 semantics.

### 3. requestDepth Recursion Guard
**Source:** Issues table `requestDepth` field
**What:** Tracks delegation depth. Prevents runaway sub-task creation.
**Port to OH:** Add `depth` to task_queue. Reject tasks beyond depth 5.
**Effort:** 30 minutes.

### 4. Forbidden Token Scanner
**Source:** `scripts/check-forbidden-tokens.mjs`
**What:** Pre-commit scan for leaked usernames, secrets, local paths in source code.
**Port to OH:** Python script in `make quality`. Scan for OS username, API key patterns, .env values.
**Effort:** 1 hour.

### 5. Commit Metrics Tracker
**Source:** `scripts/paperclip-commit-metrics.ts`
**What:** Tracks Co-Authored-By commits, LOC added/deleted, files touched by agents.
**Port to OH:** Python script querying git log for Claude co-authored commits. Feed into Conway economics.
**Effort:** 1 hour.

### 6. Session Health Tracking
**Source:** `packages/adapter-utils/src/session-compaction.ts`
**What:** Track cumulative tokens/runs/age per daemon session. Auto-reset when thresholds hit.
**Port to OH:** `SessionHealth` dataclass in shared/, check before each LLM call, reset at 2M tokens or 72h.
**Effort:** 50 lines of Python. 1-2 hours.

---

## Priority 2: High-Impact Medium Effort

### 7. Budget Gate Before Execution
**Source:** `services/budgets.ts` — `getInvocationBlock()`
**What:** Single function checks company + agent + project budgets BEFORE any work starts. Returns block reason or null.
**Port to OH:** Move budget check from inside `llm_client.generate()` to a pre-flight gate in the middleware chain. Already have middleware wired — add a budget pre-flight.
**Effort:** 4-6 hours. Create budget_policies table, implement getInvocationBlock(), wire into middleware.

### 8. Multi-Scope Budget Policies
**Source:** `schema/budget_policies.ts` — scopeType (company/agent/project), windowKind (monthly/lifetime), warnPercent, hardStopEnabled
**What:** Per-agent and per-project budgets with soft warn (80%) and hard stop (100%). Budget incidents create approval requests.
**Port to OH:** Currently budget_guard is company-level only. Add per-daemon and per-pipeline-stage policies.
**Effort:** 6-8 hours. New table, policy evaluation, incident creation.

### 9. Per-LLM-Call Cost Events with Task Attribution
**Source:** `schema/cost_events.ts` — provider, model, input/output/cached tokens, costCents, linked to agent/issue/project/goal/heartbeatRun
**What:** Every LLM call records its cost attributed to the specific task, agent, project, and goal.
**Port to OH:** Our llm_metrics tracks totals but not per-task. Add task_id and daemon_name to cost recording.
**Effort:** 3-4 hours. Extend llm_metrics table, update record_llm_call().

### 10. Promptfoo Evals for Daemon Behaviors
**Source:** `evals/promptfoo/` — YAML test cases with `contains`/`not-contains`/`javascript` assertions
**What:** Systematic testing of agent behaviors: "does the agent pick up tasks?", "does it request approval?", "does it refuse cross-boundary access?"
**Port to OH:** Test that Titan refuses emails without physical address, budget guard blocks on DB error, middleware executes in production, ClawdBot escalates to Hermes correctly.
**Effort:** 8-12 hours. Install promptfoo, template daemon prompts, write test cases.

### 11. Heartbeat Lifecycle as Injectable Skills
**Source:** `skills/paperclip/SKILL.md` — 9-step heartbeat procedure codified as agent instructions
**What:** Every agent gets a structured lifecycle protocol injected into its system prompt: identity → assignments → checkout → work → update → delegate.
**Port to OH:** Create per-daemon lifecycle documents in soul/. Inject into generate() system prompt alongside DNA.
**Effort:** 4-6 hours. Write 5 lifecycle docs (one per daemon), update generate() injection.

### 12. Adapter Interface Pattern
**Source:** `packages/adapter-utils/src/types.ts` — `ServerAdapterModule` with 12 methods
**What:** Every AI tool implements one interface: execute(), testEnvironment(), listCapabilities(), getQuotaWindows(), onHireApproved(). A registry maps type strings to implementations.
**Port to OH:** Define a Python `DaemonAdapter` protocol. Each daemon implements it. Registry dict maps names to instances. Adding Ruflo or any future agent = one interface implementation.
**Effort:** 6-8 hours. Define protocol, implement for 5 daemons, build registry.

### 13. Real-Time WebSocket Events
**Source:** `server/src/services/live-events.ts` (55 lines), `server/src/realtime/live-events-ws.ts`, `ui/src/context/LiveUpdatesProvider.tsx`
**What:** EventEmitter → WebSocket → QueryInvalidation. Push-based dashboard updates with toast rate-limiting and duplicate suppression.
**Port to OH:** Upgrade War Room from poll-based to push-based. FastAPI WebSocket routes + React Query cache invalidation.
**Effort:** 8-12 hours. Already have WebSocket hooks in War Room frontend.

---

## Priority 3: Architecture-Level Steals (Significant Effort, Transformative)

### 14. Goal Cascade with Auto-Fallback
**Source:** `schema/goals.ts` — level (company/team/agent/task), parentId self-reference; `services/issue-goal-fallback.ts` — cascade resolution
**What:** Goals are hierarchical. Tasks auto-inherit company goal when no explicit goal set. Every task knows "why" it exists.
**Port to OH:** Create goals table with levels. Wire into task_queue. "Earn $2K MRR" → "Send 5 website proposals" → "Research dentist leads in Austin".
**Effort:** 12-16 hours.

### 15. Org Hierarchy with reportsTo
**Source:** `schema/agents.ts` — `reportsTo` self-referencing FK, cycle detection, chain-of-command query, org chart SVG renderer
**What:** True corporate hierarchy. CEO delegates to CTO delegates to engineers. Role-based permissions.
**Port to OH:** Model Perseus→Titan/Hermes/ClawdBot/Conway as reporting relationships. Enable delegation chains and permission scoping.
**Effort:** 8-12 hours.

### 16. Wakeup Request Queue (Event-Driven Agents)
**Source:** `schema/agent_wakeup_requests.ts` — source (timer/assignment/on_demand/automation), idempotencyKey, coalescedCount
**What:** Agents wake on events, not fixed schedules. Task assignment triggers immediate wakeup. Coalesces duplicate wakeups.
**Port to OH:** Replace Perseus's 15 fixed scheduled tasks with event-driven wakeup. Task created → Titan wakes immediately instead of waiting for next 30s poll.
**Effort:** 16-20 hours. Fundamental scheduler rewrite.

### 17. Company-as-Exportable-Artifact
**Source:** `doc/CLIPHUB.md` — agentcompanies/v1 spec with COMPANY.md, agents/, teams/, skills/
**What:** Export the entire business config as a portable package. Import to spin up new verticals.
**Port to OH:** Export "Athena Studios" as a template. Spin up "Athena Studios Dentist Edition" or "Athena Studios Restaurant Edition" from config.
**Effort:** 20+ hours.

### 18. Routines with Concurrency/Catch-Up Policies
**Source:** `schema/routines.ts` — cron + timezone + concurrency_policy (coalesce_if_active/always_enqueue/skip_if_active) + catch_up_policy
**What:** Dynamic, user-configurable scheduled tasks that handle edge cases (what if cron fires while previous run is active?).
**Port to OH:** Replace Perseus scheduler's static task list with dynamic routines table.
**Effort:** 16-20 hours.

### 19. Governance / Approval System
**Source:** `schema/approvals.ts` — type (hire_agent/approve_strategy/budget_override), status lifecycle, comments, auto-side-effects
**What:** Generic approval workflow that gates high-risk operations. Human resolves incidents.
**Port to OH:** Replace binary review_mode with proper approval flows for budget overrides, autonomy transitions, new daemon capabilities.
**Effort:** 12-16 hours.

### 20. Agent Config Revisions (Full Audit Trail)
**Source:** `schema/agent_config_revisions.ts` — before/after snapshots, changed keys, supports rollback
**What:** Every daemon config change tracked with full before/after diff. Rollback capability.
**Port to OH:** Addresses AEGIS finding about config audit trail (QUAL-002). system_config changes get full revision history.
**Effort:** 8-12 hours.

---

## Additional Patterns Worth Noting

### From Docs/Config Agent:
21. **PARA Memory System** — structured knowledge graph with entity folders + daily notes + tacit knowledge
22. **Design Guide as Living Showcase** — `/design-guide` route as component library
23. **Calendar Versioning** — YYYY.MDD.P release scheme
24. **Token Optimization Reports** — structured post-implementation analysis docs
25. **Untrusted PR Review Container** — Docker isolation for processing hostile content

### From UI/Adapters Agent:
26. **Log Redaction** — runtime secret stripping from agent logs before storage
27. **Quota Polling** — per-adapter rate limit status with timeout protection and isolated failures

---

## Recommended Sprint Plan

### Sprint 1 (This Week): Quick Wins
Items 1-6 above. ~8 hours total. Immediate value, zero risk.

### Sprint 2 (Next Week): Budget + Cost
Items 7-9 above. ~15 hours. Addresses AEGIS financial safety findings.

### Sprint 3 (Week 3): Agent Quality
Items 10-12 above. ~18 hours. Promptfoo evals + lifecycle skills + adapter pattern.

### Sprint 4 (Month 2): Architecture
Items 14-16 above. ~40 hours. Goal cascade + org hierarchy + event-driven wakeups.

---

*Source: Paperclip (github.com/paperclipai/paperclip, MIT license)*
*Analysis: 3 parallel agents, 893 TypeScript files reviewed*
*Date: 2026-03-30*
