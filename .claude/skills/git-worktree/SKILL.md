---
name: git-worktree
description: Create and manage git worktrees for parallel Perseus development
triggers:
  - worktree
  - parallel branch
  - work on multiple branches
---

# Git Worktree Skill

## When to Use
Agent should trigger when:
- User wants to work on multiple branches simultaneously
- Need isolated environment for a daemon feature
- Testing something without affecting current branch

## Instructions
1. Create worktree: `git worktree add ../worktree-[name] [branch]`
2. List worktrees: `git worktree list`
3. Remove worktree: `git worktree remove ../worktree-[name]`

## Patterns
- Always create worktrees in parent directory
- Name descriptively: `worktree-titan-scoring`, `worktree-hermes-dashboard`
- Clean up worktrees when done
- Never delete the main worktree

## Validation
After creating worktree, verify:
- Directory exists
- Correct branch checked out
- Clean working tree
