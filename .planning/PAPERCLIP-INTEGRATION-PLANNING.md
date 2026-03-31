# Paperclip Infrastructure Integration — Mega-Plan Input

**Goal:** Implement the top 18 Paperclip infrastructure patterns into Objective Hertz across 4 sprints.
**Source:** intel/paperclip-teardown.md (27 patterns analyzed, 18 selected for implementation)
**Type:** Utility (infrastructure upgrade to existing system)
**Maturity:** CLEAR REQUIREMENTS — skip Seed, skip discovery. Go straight to debate + planning.

## Sprint Breakdown (from teardown analysis)

### Sprint 1: Quick Wins (6 items, ~8 hours)
1. Agent state machine with pause reasons (status enum + pause_reason on daemon health)
2. Atomic task checkout (claimed_by + claimed_at with 409 semantics)
3. requestDepth recursion guard (depth field on task_queue, reject > 5)
4. Forbidden token scanner (pre-commit script scanning for leaked secrets/usernames)
5. Commit metrics tracker (git log analysis for Co-Authored-By commits)
6. Session health tracking (cumulative tokens/runs/age per daemon, auto-reset at thresholds)

### Sprint 2: Budget + Cost (3 items, ~15 hours)
7. Budget gate before execution (getInvocationBlock pattern — check before any work)
8. Multi-scope budget policies (per-agent + per-project with soft warn/hard stop)
9. Per-LLM-call cost events with task attribution (extend llm_metrics)

### Sprint 3: Agent Quality (3 items, ~18 hours)
10. Promptfoo behavioral evals (YAML test cases for daemon behavior across models)
11. Heartbeat lifecycle as injectable skills (per-daemon lifecycle docs in soul/)
12. Adapter interface pattern (DaemonAdapter protocol + registry)

### Sprint 4: Architecture (3 items, ~40 hours)
13. Goal cascade with auto-fallback (company → team → agent → task goals)
14. Org hierarchy with reportsTo (daemon reporting chain + role permissions)
15. Event-driven wakeup queue (replace fixed scheduler with event-driven invocation)

## Key Constraints
- All patterns from MIT-licensed Paperclip (github.com/paperclipai/paperclip)
- Must integrate with existing 5-daemon architecture (Perseus, Titan, Hermes, ClawdBot, Conway)
- Must not break existing 457 tests
- Feature flags for all behavioral changes
- AEGIS guardrails in CLAUDE.md must be respected (SQL parameterization, credential stripping, etc.)

## Existing Infrastructure to Build On
- shared/db.py — Postgres pool (add budget_policies, cost_events tables)
- shared/middleware.py — 6-middleware chain (add budget pre-flight)
- shared/llm_client.py — LLM calls with budget checking (extend cost tracking)
- shared/agent_base.py — Agent lifecycle hooks (add state machine)
- perseus/scheduler.py — Fixed 15-task scheduler (eventually replace with wakeup queue)
- shared/observability.py — LLM metrics (extend with task attribution)
- soul/ — Daemon personality files (add lifecycle protocols)

## Execution Instructions
Run: `/mega-plan` with this file as input
Then: `/gsd:autonomous` to execute all phases
After each sprint: `/review` + `/qa` + `/ship`
