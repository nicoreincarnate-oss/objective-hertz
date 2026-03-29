---
name: meta-skill
description: Coordinate and manage all Perseus skills, agents, and prompts
triggers:
  - meta
  - use skill
  - list skills
  - manage skills
---

# Perseus Meta Skill

## Purpose
Single coordination point for all skills, agents, and prompts.

## Available Skills
- **run-tests** — Ruff + pytest quality checks
- **git-worktree** — Parallel branch development
- **migrate-database** — Safe Postgres migrations
- **start-stop-app** — Daemon lifecycle management
- **meta-skill** — This skill (coordination)

## Available Agents
- **orchestrator** — Fleet management across 4 daemons
- **scout_agent** — Research and evaluation
- **review_agent** — Code review with Perseus context
- **test_writer_agent** — Pytest test generation
- **fetch_docs_agent** — Documentation gathering

## Available Commands
- `/prime` — Load project context
- `/plan` — Create implementation plan
- `/build` — Execute from plan
- `/review` — Review changes
- `/test_backend` — Full quality checks
- `/health-check` — System health
- `/damage-control` — Protection status
- `/reproduce_bug` — Bug reproduction
- `/context_audit` — Token usage

## CLI Teaching Prompts
- `tools/test_cli` — Perseus test commands
- `tools/database_cli` — Postgres operations
- `tools/git_cli` — Git operations
- `tools/deploy_cli` — Deployment operations

## Creating New Skills
1. Copy `.claude/skills/_template/` to `.claude/skills/[new-name]/`
2. Edit SKILL.md
3. Add trigger keywords
4. Test by asking the agent to use it
