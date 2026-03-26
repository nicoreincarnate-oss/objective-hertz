---
description: Load Perseus project context and prepare for work
---

# Prime Context Loader

## Purpose
Load essential Perseus context before starting any work session.

## Instructions
1. Read `CLAUDE.md` to understand the 4-daemon architecture and agent instructions
2. Read `HANDOFF.md` for current build state and known limitations
3. Check `specs/` for any active plans or specifications
4. Check `app_reviews/` for recent review findings
5. Check `logs/` for recent daemon logs and maintenance results
6. Run `make status` to check current daemon state
7. Report a brief status summary:
   - Active specs/plans found
   - Recent review findings
   - Daemon status (running/stopped)
   - Docker services status
   - Context window usage estimate

## Output
Provide a 3-5 line status report, then ask what the user wants to work on.

## Rules
- Do NOT start building anything during prime
- Do NOT modify any files during prime
- This is READ-ONLY context loading
