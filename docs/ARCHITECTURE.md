# PERSEUS Architecture

## System Overview

```
                    PERSEUS (Master Daemon)
                   /       |        \
              TITAN    HERMES    OPENCLAW
           (revenue) (intel)    (skills)
                |
    ┌───────────┼──────────────────────────┐
    │    10-Stage Autonomous Pipeline       │
    │                                       │
    │  discover → research → compose →      │
    │  send → follow_up → demo_site →       │
    │  close → build → deploy → invoice     │
    └───────────────────────────────────────┘
```

## Port Table

| Service | Port | Type |
|---------|------|------|
| Postgres | 5432 | Docker |
| Qdrant | 6333 | Docker |
| Mem0 | 8888 | Docker |
| N8N | 5678 | Docker |
| Ollama | 11434 | Native |
| Web Dashboard | 8500 | Native (FastAPI) |
| Hermes Agent | — | Native (Nous Research) |
| OpenClaw | — | Native (macOS app) |

## Data Flow

1. **Perseus** inserts tasks into `task_queue` on schedule
2. **Titan** polls `task_queue`, claims tasks, executes pipeline stages
3. Pipeline stages read/write leads in `clients` table, track status via state machine
4. **Titan** emits events to `events` table when things happen
5. **Hermes** polls `events`, sends Telegram alerts to Nico
6. **Review queue** holds items for Nico's approval (first 10 sales)
7. **Titan learnings** table stores what the system learns from every interaction

## LLM Routing

| Task | Model | Why |
|------|-------|-----|
| Email composition | Claude Haiku | Quality + speed, ~$40/mo for 1000/day |
| Proposals, strategy | Claude Sonnet | Best quality for important decisions |
| Classification, extraction | Ollama Qwen2.5 14B | Free, local, fast enough |
| Embeddings | Ollama nomic-embed-text | Free, local |

## Memory Architecture

- **Postgres** — Structured data (leads, deals, metrics, tasks, events)
- **Qdrant** — Vector search (semantic memory, lead similarity)
- **Mem0** — Agent memory (conversation context, learned insights)
- **titan_learnings** table — Structured insights from daily/weekly reflection
