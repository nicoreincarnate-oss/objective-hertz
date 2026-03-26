---
name: start-stop-app
description: Start, stop, and manage Perseus daemons
triggers:
  - start perseus
  - stop perseus
  - restart daemons
  - start server
  - daemon management
---

# Perseus Daemon Management Skill

## When to Use
Agent should trigger when:
- Need to start/stop daemons for testing
- Need to restart after code changes
- Checking if daemons are running

## Instructions
### Start
1. Start Docker: `make up`
2. Wait for services: `make health`
3. Start daemons: `make start`
4. Verify: `make status`

### Stop
1. Stop daemons: `make stop`
2. Verify: `make status`

### Restart
1. `make restart`
2. Verify: `make status`

### Docker Only
- Start: `make up`
- Stop: `make down`
- Status: `docker compose ps`
