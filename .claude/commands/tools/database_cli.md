---
description: CLI-teaching prompt for Perseus database operations
---

# Perseus Database CLI

## Purpose
Interact with Perseus Postgres database via CLI. Saves ~10K tokens vs loading a database MCP.

## Connection
```bash
# Via docker exec (recommended)
docker exec perseus-postgres psql -U perseus -d perseus -c "YOUR QUERY"

# Direct (if psql installed locally)
PGPASSWORD=perseus_secure_2026 psql -h localhost -U perseus -d perseus -c "YOUR QUERY"
```

## Common Queries
```bash
# List tables
docker exec perseus-postgres psql -U perseus -d perseus -c "\dt"

# Describe table
docker exec perseus-postgres psql -U perseus -d perseus -c "\d+ task_queue"

# Pending tasks
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT id, task_type, status, created_at FROM task_queue WHERE status='pending' ORDER BY created_at DESC LIMIT 20;"

# Lead pipeline status
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT status, count(*) FROM leads GROUP BY status ORDER BY count DESC;"

# Recent events
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT event_type, daemon, created_at FROM events ORDER BY created_at DESC LIMIT 20;"

# System config
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT key, value FROM system_config;"

# Titan learnings
docker exec perseus-postgres psql -U perseus -d perseus -c "SELECT category, summary, created_at FROM titan_learnings ORDER BY created_at DESC LIMIT 10;"
```

## Backup
```bash
make backup
# or manually:
docker exec perseus-postgres pg_dump -U perseus perseus > backup_$(date +%Y%m%d).sql
```

## Rules
- NEVER run DROP, TRUNCATE, or DELETE without confirmation (damage control blocks these)
- Always use LIMIT on SELECT queries
- Prefer read-only operations unless explicitly building something
- Use `make backup` before any destructive operation
