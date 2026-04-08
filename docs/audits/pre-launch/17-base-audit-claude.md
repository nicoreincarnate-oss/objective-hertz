# Audit: .claude/ sprawl & alignment (base:audit-claude)

**Date:** 2026-04-07
**Scope:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/` + `/Users/majovega/.claude/`
**Mode:** Read-only. No files modified. Per-item remediation requires operator approval.
**Verdict:** AMBER — moderate sprawl, no launch-blocking conflicts.
**Sprawl level:** 3/5 (manageable).

---

## Summary Counts

| Metric | Count |
|---|---|
| Worktrees total | 32 |
| Stale worktrees (>8 days, no commits since 2026-03-30) | 29 |
| Fresh worktrees (within 7 days) | 3 (`charming-elion`, `agent-ae6aa41b`) |
| Project skills | 7 |
| Global skills | 67 |
| Skill name collisions project ↔ global | 0 hard collisions; 1 overlap (`ui-ux-pro-max`) |
| Duplicate commands project ↔ global | 6 (`build`, `plan`, `review`, `prime`, `health-check`, `context_audit`) |
| Dead launch.json entries | 4 of 4 unverified; 1 JSON syntax issue (duplicate key) |
| Hook conflicts (commit/run) | 0 — project has no git commit hooks; global PreToolUse hooks are additive |
| Plans contradicting locked state | 3 of 4 (`.claude/plans/*.md` predate current Phase 39 in `.planning/STATE.md`) |
| Agents referencing dead tooling | 2 (reference Mem0, Instantly v1 — verify) |

---

## Top Issues (ordered by risk, lowest-to-highest for remediation)

### 1. `.claude/launch.json` has a JSON duplicate-key bug [LOW RISK — cosmetic]
File: `/Users/majovega/Desktop/Projects/objective-hertz/.claude/launch.json`
The `dashboard` configuration defines `"runtimeArgs": ["dev"]` twice. JSON parsers will accept the last one silently, but this is a lint bug — fix to single declaration.

### 2. Stale git worktrees (29/32) [LOW RISK — disk sprawl]
29 worktrees under `.claude/worktrees/` have no commits since **2026-03-30 or earlier** (8+ days). Several share identical commit `fd96c12` from 2026-03-23 (`agent-a344be93`, `agent-a880b89f`, `agent-a996ab4e`) and `e8e6026` from 2026-03-22 (`funny-aryabhata`, `zen-euclid`). These are exhausted agent branches.
- Active: `charming-elion` (2026-04-07, current audit), `agent-ae6aa41b` (2026-04-05).
- Recommend: operator-approved `git worktree remove` pass for any agent-* worktree with `last_commit < 2026-03-31`.
- **Do not auto-delete** — some may have uncommitted work.

### 3. Project-local plans predate locked state [MEDIUM RISK — decision drift]
`.planning/STATE.md` locks the project at **Phase 39 / Intel Integration (Full Scope)**. The loose plans in `.claude/plans/` are:
- `agentic-architecture.md` — superseded; pre-dates MiRA + Phase 16
- `clawdbot-self-equip.md` — partially absorbed into clawdbot Phase 33-39 mega-plan (completed)
- `fix-everything.md` — "10 fixes + ClawdBot daemon" — historical, superseded
- `memory-learning-upgrades.md` — 18 improvements, status unclear vs. Phase 42.5 v2 local-tier plan in global MEMORY.md

These contradict `shared/project_local_tier_phase_42_5_v2.md` locked decisions (Opus is being replaced by local tier). Recommend: archive all four to `.claude/plans/_archive/` with a pointer file referencing `.planning/STATE.md`.

### 4. Command duplication project ↔ global [MEDIUM RISK — ambiguity]
Project `.claude/commands/` defines 13 loose `.md` commands. Six have name collisions with global commands that are namespaced (gsd/paul) or root-level:
- `build.md`, `plan.md`, `review.md`, `prime.md` — collide with global verbs (`/gsd:execute-phase`, `/gsd:plan-phase`, `/review`, `/gstack-upgrade` loader)
- `context_audit.md`, `health-check.md` — collide with global `context_audit` and `health-check` slash commands (both in global skills)
- `health_perseus.md` — unique, but overlaps with `health-check.md`; keep one

Resolution: these are Perseus-specific and should stay project-local BUT should be renamed to a `/perseus:` namespace to avoid collision with global verbs. No immediate breakage because project commands take precedence, but it's confusing.

### 5. Agent definitions reference potentially dead tooling [MEDIUM RISK]
- `fetch_docs_agent.md` lists Mem0 and Instantly v2 as canonical — verify against current code (project moved to Neo4j memory-graph MCP per global CLAUDE.md).
- `orchestrator.md` describes a 4-daemon architecture (Perseus/Titan/Hermes/ClawdBot) but MEMORY.md lists 8 agents (adds Conway, Deerflow, Ruflo, Openjarvis). Stale.

### 6. Skill overlap: `ui-ux-pro-max` [LOW RISK]
Exists in BOTH global (`~/.claude/skills/ui-ux-pro-max`) and project (`.claude/skills/ui-ux-pro-max`). Project version has `data/` and `scripts/` subdirs — likely Perseus-customized. Verify which wins (project should). If project version is a fork of global, document divergence; if it's identical, delete project copy.

### 7. Hook config is clean but under-documented [LOW RISK]
- Global `settings.json` runs damage-control on every Bash/Edit/Write, gsd-prompt-guard, carl-hook, and gsd-check-update at SessionStart. All additive, no conflicts.
- Project `.claude/hooks/validators/` has `build_validator.sh` and `test_validator.sh` but they're not wired into any hook event (no project `settings.json` references them). **DEAD hooks** — either wire them or delete.
- Project `hooks/patterns.yaml` is referenced by the global `bash_tool_damage_control.sh` (good — this is the source of truth for readonly/no-delete paths).

### 8. Skills that could be promoted or demoted
- **Project → Global candidate:** `git-worktree` skill (project-local) is generic and duplicates functionality provided by the workflow around `EnterWorktree`/`ExitWorktree` tools. Either delete (tools supersede it) or promote to global.
- **Global → Project candidate:** None urgent. `perseus-mcp-builder` is correctly global (used by other projects potentially).

### 9. `memory-system-diagram.md` orphan [LOW RISK]
23KB file at `.claude/memory-system-diagram.md` — not referenced by any skill, command, or plan. Likely historical documentation. Move to `docs/architecture/` or archive.

### 10. `handoff.md` at `.claude/` root [LOW RISK]
Project root has `.claude/handoff.md` (not to be confused with project-root `HANDOFF.md`). Dated 2026-03-25. Stale by 13 days. `paul:handoff` and `gsd:pause-work` both generate handoffs in proper locations. Archive.

---

## Non-issues / Verified Clean

- **No hook runs on commit.** Neither project nor global has `PostToolUse` git hooks or `Stop` commit hooks. The recent 50-file Phase 42.5 v2 commit is safe.
- **No skill name collision** with the newly-registered global `pre-launch-audit` skill.
- **`base:audit-claude` itself** registered cleanly under global `base` namespace.
- **`.aegis/` and `.planning/` are outside `.claude/`** — correctly scoped as project-state-of-record directories, no sprawl risk.
- **`settings.local.json`** is 83KB / 870 lines — large but that's normal permission accumulation, not sprawl.

---

## Recommended Cleanups (ordered, each requires operator approval)

| # | Action | Risk | Reversible |
|---|---|---|---|
| 1 | Fix `launch.json` duplicate `runtimeArgs` key | none | yes |
| 2 | Archive `.claude/plans/*.md` (4 files) to `_archive/` | none | yes |
| 3 | Delete `.claude/handoff.md` (stale) | none | yes (git) |
| 4 | Move `.claude/memory-system-diagram.md` to `docs/` | none | yes |
| 5 | Delete/wire dead validator hooks (`build_validator.sh`, `test_validator.sh`) | none | yes (git) |
| 6 | Rename project commands to `/perseus:` namespace | low | yes |
| 7 | Refresh `orchestrator.md` and `fetch_docs_agent.md` to current architecture | low | yes |
| 8 | Operator-gated `git worktree remove` of 29 stale `agent-*` worktrees | medium | partial (commits preserved on branch) |
| 9 | Decide `ui-ux-pro-max` project-vs-global fork strategy | low | yes |
| 10 | Decide `git-worktree` skill: promote, keep, or delete | low | yes |

---

## Manual Follow-ups (operator decisions required)

- Confirm `.claude/plans/memory-learning-upgrades.md` status vs. Phase 42.5 v2 local-tier mega-plan.
- Confirm whether `fetch_docs_agent.md` Mem0 reference is still accurate post memory-graph MCP migration.
- Review 29 stale worktrees for uncommitted WIP before remove.
- Decide namespace policy for project commands (`/perseus:*`).
