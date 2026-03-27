---
description: CLI-teaching prompt for Perseus deployment operations
---

# Perseus Deploy CLI

## Pre-Deploy Checklist
1. All tests pass (`make quality`)
2. Review completed (`/review`)
3. Daemons stopped (`make stop`)
4. Database backed up (`make backup`)

## Operations
```bash
# Start all daemons
make start

# Stop all daemons
make stop

# Restart all daemons
make restart

# Start Docker services only
make up

# Stop Docker services
make down

# Check status
make status

# Check health
make health

# View logs
make logs

# Backup database
make backup
```

## Rules
- NEVER deploy without running the pre-deploy checklist
- Always stop daemons before major code changes
- Always backup database before schema changes
- Damage control will block dangerous deploy commands
