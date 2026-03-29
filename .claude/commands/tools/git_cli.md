---
description: CLI-teaching prompt for git operations
---

# Git CLI Teaching Prompt

## Purpose
Standardized git operations for Perseus agents.

## Common Operations
```bash
git status                          # Working tree status
git diff                            # Unstaged changes
git diff --staged                   # Staged changes
git diff main...HEAD                # All changes vs main
git log --oneline -20               # Recent commits
git checkout -b feature/name        # New branch
git add path/to/file                # Stage specific file
git commit -m "type: description"   # Commit
```

## Commit Message Convention
```
type: short description

Types: feat, fix, refactor, test, docs, chore, style, perf
Examples:
  feat: add lead scoring to Titan pipeline
  fix: resolve state machine transition bug in close_deal
  refactor: extract shared config validation
  test: add budget gate threshold tests
```

## Rules
- Never force push to main/master
- Always check status before committing
- Use descriptive branch names
- Commit logical units of work
- Run `make quality` before pushing
