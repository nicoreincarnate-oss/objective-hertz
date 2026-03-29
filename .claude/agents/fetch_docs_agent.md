---
description: Fetch and organize documentation for Perseus dependencies
---

# Fetch Docs Agent

## Purpose
Pull documentation from external sources and organize it in AI_docs/.

## Instructions
1. Identify what documentation is needed based on Perseus's stack:
   - psycopg v3 (async Postgres)
   - FastAPI + Uvicorn
   - Mem0 vector memory
   - Instantly.ai API v2
   - Stripe API (invoicing)
   - Firecrawl API
   - Telegram Bot API
   - Ollama API
   - Next.js (dashboard)
2. Search for and retrieve relevant documentation
3. Save organized docs to AI_docs/
4. Create AI_docs/INDEX.md listing all docs

## Rules
- Only fetch documentation relevant to current task
- Keep docs concise — summaries over full dumps
- Update INDEX.md with each new addition
