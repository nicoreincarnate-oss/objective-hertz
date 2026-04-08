# Pre-Launch Audit Run — Phase 42.5 v2

**Scheduled**: 2026-04-07 23:30 MST (one-shot, fires on next Claude Code idle window)
**Target wall clock**: 2-3 hours via parallel agent batches
**Total audits**: 19 (1 deferred to Studio Day 1)
**Output**: `docs/audits/pre-launch/SUMMARY.md` + per-skill findings

---

## What this is

The operator scheduled an autonomous overkill pre-launch audit pass over 19
audit/check/verification skills. The goal: stress-test the Phase 42.5 v2
implementation (50 files written autonomously in commit 30770c0 + evening
update 53dfecc) before launch on the Mac Studio.

When this fires, a fresh Claude Code session executes the orchestrator prompt
below. The session runs all 19 skills in 5 parallel batches, captures findings
to disk, and synthesizes a single `SUMMARY.md` with a SHIP / SHIP-WITH-FIXES /
BLOCK LAUNCH verdict.

---

## The 19 audits + execution batches

### Sequential foundation (Batch 0 — runs first)
- `/igus` — master audit orchestrator
- `/aegis:init && /aegis:audit` — codebase scanners (Semgrep, Trivy, Gitleaks, Checkov, Syft, Grype)

### Parallel batch 1 — Security (4 agents)
- `/cso` — Chief Security Officer mode
- `/aegis:remediate` — fix plans for AEGIS findings
- `/aegis:guardrails` — generate project rules from findings
- `/misalignment-detector` — drift between memory and code

### Parallel batch 2 — Architecture + Quality (5 agents)
- `/paul:audit` — enterprise architectural audit on the implementation
- `/plan-eng-review` — execution risk on the code
- `/quality-gate` — anti-slop 5-dimension scoring
- `/simplify` — dead code, redundant patterns
- `/engineering:tech-debt` — shortcuts taken, debt enumeration

### Parallel batch 3 — Operational (3 agents)
- `/operations:risk-assessment` — forward-looking risks
- `/engineering:deploy-checklist` — boring sanity check
- `/gsd:audit-uat` — outstanding test items across all phases

### Parallel batch 4 — External + Long-form (5 agents)
- `/gsd:review` — cross-AI peer review (external CLIs)
- `/failure-miner` — patterns from past failures
- `/base:audit-claude` — .claude/ sprawl, duplicate skills
- `/think-at-n` — 3-5 parallel reviewer perspectives
- `/anthropic-skills:repo-build-sheet` — enterprise build sheet

### Deferred to Studio Day 1
- `/health-check` — needs running daemons, runs after BUILD step on Studio

---

## Orchestrator prompt (this is what the scheduled task fires)

```
PRE-LAUNCH AUDIT ORCHESTRATION — Phase 42.5 v2 Perseus

You are running an autonomous pre-launch audit pass on the Phase 42.5 v2
implementation. The operator scheduled this to run while they sleep so they
wake up to a complete audit report. Be exhaustive but efficient — use parallel
agent batches wherever possible.

## Context

Project: Perseus (objective-hertz) — autonomous business system on Mac Studio M4 Max
Worktree: /Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion
Branch: claude/charming-elion
Commits to audit: 30770c0 (40 files initial), 53dfecc (evening update)
Phase: 42.5 v2 — Local Tier Hardening (quality-first, all on Mac Studio)

Source of truth files (READ THESE FIRST):
1. /Users/majovega/.claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory/project_local_tier_phase_42_5_v2.md
2. The worktree's .paul/HANDOFF.md
3. The worktree's .paul/STATE.md (locked operator decisions)
4. The worktree's docs/runbooks/cutover-playbook.md
5. The worktree's docs/runbooks/native-services-setup.md
6. git show 30770c0 --stat AND git show 53dfecc --stat (the two commits to audit)
7. git diff e8e6026 HEAD --stat (full diff being launched)

## Rules

- Use absolute paths everywhere in tool calls (per CARL global rule 0)
- Run independent tool calls in parallel (per CARL global rule 1) — batch agents
- Write findings to disk as you go, don't lose work if a step fails
- Hard cap: 4 hours total. Stop and synthesize whatever you have at 4h.
- If a skill errors, doesn't exist, or hangs: catch, log, move on. Don't block the run.

## The 19 audits in 5 batches

### Batch 0 — Sequential foundation (must run first)

1. Invoke /igus skill — pass it the worktree path and the source-of-truth files. Save findings to docs/audits/pre-launch/01-igus.md

2. Invoke /aegis:init then /aegis:audit on the worktree. AEGIS produces phase reports — capture all of them to docs/audits/pre-launch/02-aegis/

### Batch 1 — Parallel security (spawn 4 agents in ONE message)

Run these as parallel Agent invocations (general-purpose subagents). Each agent invokes one skill via the Skill tool, captures findings to disk:

- Agent A: /cso → docs/audits/pre-launch/03-cso.md
- Agent B: /aegis:remediate → docs/audits/pre-launch/04-aegis-remediate.md
- Agent C: /aegis:guardrails → docs/audits/pre-launch/05-aegis-guardrails.md
- Agent D: /misalignment-detector → docs/audits/pre-launch/06-misalignment.md

### Batch 2 — Parallel architecture + quality (5 agents in parallel)

- Agent E: /paul:audit on the implementation → docs/audits/pre-launch/07-paul-audit.md
- Agent F: /plan-eng-review on the code → docs/audits/pre-launch/08-plan-eng-review.md
- Agent G: /quality-gate on the diff → docs/audits/pre-launch/09-quality-gate.md
- Agent H: /simplify → docs/audits/pre-launch/10-simplify.md
- Agent I: /engineering:tech-debt → docs/audits/pre-launch/11-tech-debt.md

### Batch 3 — Parallel operational (3 agents in parallel)

- Agent J: /operations:risk-assessment → docs/audits/pre-launch/12-risk-assessment.md
- Agent K: /engineering:deploy-checklist → docs/audits/pre-launch/13-deploy-checklist.md
- Agent L: /gsd:audit-uat → docs/audits/pre-launch/14-gsd-audit-uat.md

### Batch 4 — Parallel external + long-form (5 agents in parallel)

- Agent M: /gsd:review (external cross-AI) → docs/audits/pre-launch/15-gsd-review.md
- Agent N: /failure-miner → docs/audits/pre-launch/16-failure-miner.md
- Agent O: /base:audit-claude → docs/audits/pre-launch/17-base-audit-claude.md
- Agent P: /think-at-n with 3 parallel reviewer angles → docs/audits/pre-launch/18-think-at-n.md
- Agent Q: /anthropic-skills:repo-build-sheet → docs/audits/pre-launch/19-build-sheet.md

## Final synthesis

After all batches complete (or timeout), produce a single comprehensive report
at docs/audits/pre-launch/SUMMARY.md with this structure:

# Pre-Launch Audit Summary — Phase 42.5 v2

## Verdict: SHIP / SHIP-WITH-FIXES / BLOCK LAUNCH
[One paragraph of why]

## Critical findings (P0 — must fix before launch)
[Consolidated and ranked across all 19 audits, deduplicated]

## Major findings (P1 — should fix before launch)

## Minor findings (P2 — fix in first week post-launch)

## Tech debt accepted (P3 — backlog)

## What's working well (preservation list)

## Recommended fix order (ROI ranked)

## Operator decisions needed before launch
[Anything that needs the operator to decide before the fix can land]

## Audit-by-audit results
| # | Skill | Verdict | Critical | Major | Minor | File |
|---|---|---|---|---|---|---|

## Coverage gaps
[What none of the 19 audits looked at]

## Comparison to original Phase 42.5 v2 plan
[Did the implementation drift from the spec? Where?]

## Recommended next action
[Concrete: do X, then Y, then Z]

## Time + cost
- Wall clock: X hours
- Sub-agent invocations: N
- Skills that errored: [list]

## Commit + handoff

After SUMMARY.md is written:

1. git -C /Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion add docs/audits/pre-launch/

2. git commit -m "Pre-launch audit: 19-skill autonomous audit pass

Verdict: <SHIP|SHIP-WITH-FIXES|BLOCK>
Critical findings: N
Major findings: N

See docs/audits/pre-launch/SUMMARY.md for full report.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

3. Update .paul/HANDOFF.md with a new section at the top:
   ## PRE-LAUNCH AUDIT COMPLETE — <timestamp>

   Verdict: <SHIP|SHIP-WITH-FIXES|BLOCK>
   Read first: docs/audits/pre-launch/SUMMARY.md
   Critical: <count>
   Recommended next: <action>

4. Print a summary to stdout so the next time the operator opens this session
   they see it: "PRE-LAUNCH AUDIT DONE. Verdict: X. Read docs/audits/pre-launch/SUMMARY.md"

## Failure handling

If you hit any of these, save what you have and continue:
- Skill doesn't exist → log "skill not available", move on
- Skill errors → log error, save partial output, move on
- Skill takes >30 min → kill it via TaskStop, save partial output, move on
- Out of context budget → write what you have, commit, exit gracefully

If you hit the 4-hour hard cap, do this in the last 10 minutes:
1. Stop all running agents via TaskStop
2. Write SUMMARY.md with whatever you have, marked "PARTIAL — hit time cap"
3. Commit it
4. Update HANDOFF.md noting the partial run

The operator will read HANDOFF.md first thing in the morning. They need to know
the verdict and the top 3 critical findings within the first 30 seconds of
reading.

GO.
```

---

## Manual execution (if the scheduled task fails to fire)

If you wake up and the audit didn't run automatically, execute it manually:

```bash
cd /Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion
# Open Claude Code in this worktree
# Paste the contents of the "Orchestrator prompt" section above into a new conversation
```

The prompt is fully self-contained — a fresh Claude session with no prior context can execute it.

---

## Why this approach

- **One scheduled task, not 19**: shared context across batches, single synthesis at the end
- **Parallel agent batches**: 19 audits in ~2-3 hours wall clock instead of ~6 hours sequential
- **Disk-write checkpointing**: every audit's findings hit disk immediately, no work lost on failure
- **4-hour hard cap**: bounded so it never runs forever
- **Self-contained prompt**: fresh session can execute without any prior conversation context
- **Failure-tolerant**: any one skill failing doesn't block the rest
- **Single source of truth**: SUMMARY.md is the only file the operator needs to read first
