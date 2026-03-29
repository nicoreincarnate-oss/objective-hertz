---
description: List all active damage control protections
---

# Damage Control Status

## Purpose
Show all active protections in the Perseus project.

## Instructions
1. Read `.claude/hooks/patterns.yaml`
2. Read `.claude/settings.local.json` to verify hooks are wired
3. Report:
   - Number of blocked command patterns
   - Number of ask-before-run patterns
   - Zero-access paths
   - Read-only paths
   - No-delete paths
   - Whether pre-tool-use hooks are active
   - Whether post-tool-use hooks are active

## Output Format
```
PERSEUS DAMAGE CONTROL STATUS
==============================
Pre-tool hooks:   [ACTIVE/INACTIVE]
Post-tool hooks:  [ACTIVE/INACTIVE]
Blocked patterns: [N] (rm -rf, DROP TABLE, force-push, stripe delete, etc.)
Ask patterns:     [N] (DELETE FROM, docker rm, git reset --hard, etc.)
Zero-access:      [list]
Read-only:        [list]
No-delete:        [list]
```
