---
description: Full Perseus system health — daemons, infrastructure, database, code quality
---

# Perseus Full Health Check

## Purpose
Complete system-level health check covering all layers of Perseus.

## Instructions
Run these checks in order:

### 1. Infrastructure
```bash
make health    # Postgres, Qdrant, Mem0, N8N, Ollama
```

### 2. Daemons
```bash
make status    # Perseus, Titan, ClawdBot, Hermes, Dashboard
```

### 3. Database
```bash
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT count(*) as pending_tasks FROM task_queue WHERE status='pending';"
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT status, count(*) FROM leads GROUP BY status;"
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT count(*) as total_events FROM events WHERE created_at > now() - interval '24 hours';"
```

### 4. Code Quality
```bash
ruff check shared perseus titan hermes clawdbot
PYTHONPATH=. python3 -m pytest tests/ -q --ignore=tests/openjarvis
```

### 5. Damage Control
- Verify `.claude/hooks/patterns.yaml` exists
- Verify hooks wired in `.claude/settings.local.json`
- Count blocked vs ask patterns

### 6. Logs
```bash
tail -5 logs/perseus.log logs/titan.log logs/clawdbot.log 2>/dev/null
```

## Output
```
PERSEUS FULL HEALTH CHECK
=========================
Infrastructure:  [OK/WARN/FAIL]
  Postgres:      [status]
  Qdrant:        [status]
  Mem0:          [status]
  Ollama:        [status]

Daemons:         [OK/WARN/FAIL]
  Perseus:       [RUNNING/STOPPED]
  Titan:         [RUNNING/STOPPED]
  ClawdBot:      [RUNNING/STOPPED]
  Hermes:        [RUNNING/STOPPED]

Database:        [OK/WARN/FAIL]
  Pending tasks: [N]
  Lead pipeline: [breakdown by status]
  24h events:    [N]

Code Quality:    [OK/WARN/FAIL]
  Ruff:          [clean/N issues]
  Tests:         [N pass / N fail]

Damage Control:  [OK/WARN/FAIL]
  Hooks:         [ACTIVE/INACTIVE]
  Patterns:      [N blocked, N ask]

Recommendations: [prioritized list]
```
