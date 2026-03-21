---
name: perseus-status
description: Check Perseus system status — pipeline, revenue, agents, email stats.
version: 1.0.0
triggers:
  - status
  - how is perseus
  - system status
  - what's happening
---

# Perseus Status

Check the current state of the Perseus system by querying the dashboard API.

## Instructions

When the user asks for status, system health, or what's happening:

1. Call the Perseus dashboard API at http://localhost:8500/api/pipeline to get pipeline data
2. Call http://localhost:8500/api/health to get agent health
3. Present a concise summary:
   - Total leads and their stages
   - Emails sent today
   - Revenue collected and pending
   - Agent health (Perseus, Titan, Hermes status)
   - Review queue count (if review mode is active)

Keep it brief. Nico doesn't want walls of text.
