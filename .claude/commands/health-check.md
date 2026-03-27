---
description: Comprehensive Perseus system health assessment
---

# Health Check

## Purpose
Full system health assessment covering code, infrastructure, and daemons.

## Instructions
1. Check daemons: `make status`
2. Check Docker services: `make health`
3. Check code quality: `ruff check shared perseus titan hermes clawdbot`
4. Check tests: `PYTHONPATH=. python3 -m pytest tests/ -q --ignore=tests/openjarvis`
5. Check for TODO/FIXME: `grep -rn "TODO\|FIXME\|HACK" --include=*.py perseus/ titan/ hermes/ clawdbot/ shared/`
6. Check database: `docker exec perseus-postgres pg_isready`
7. Check documentation: Is CLAUDE.md up to date? Is HANDOFF.md current?
8. Check git status
9. Check damage control: Are hooks wired? Is patterns.yaml present?
10. Check logs for errors: `tail -20 logs/*.log`

## Output
Write report to logs/health_[timestamp].md

```
PERSEUS HEALTH CHECK
====================
Daemons:         [OK/WARN/FAIL] (perseus/titan/hermes/clawdbot)
Docker:          [OK/WARN/FAIL] (postgres/qdrant/mem0/n8n/ollama)
Code Quality:    [OK/WARN/FAIL]
Tests:           [OK/WARN/FAIL]
Database:        [OK/WARN/FAIL]
Documentation:   [OK/WARN/FAIL]
Git Status:      [OK/WARN/FAIL]
Damage Control:  [OK/WARN/FAIL]

Details: [per section]
Recommendations: [prioritized list]
```
