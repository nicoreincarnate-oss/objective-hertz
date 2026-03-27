> **Historical**: This architecture document reflects an earlier Perseus-centric design. The system now uses OpenJarvis as the orchestrator framework.

# PERSEUS Architecture

## System Overview

```
                 HERMES AGENT (gateway + memory + skills)
                              |
                    PERSEUS (Master Orchestrator)
                   /       |        \
              TITAN   CLAWDBOT   DASHBOARD API
           (revenue) (skills+hands)   (:8500)
                |                      |
    ┌───────────┼────┐                 └── War Room (React frontend :3000)
    │  10-Stage      │
    │  Pipeline      │
    │  discover →    │
    │  research →    │   CLAWDBOT:
    │  compose →     │   ├── 26 capability categories
    │  send →        │   ├── 3 skill registries
    │  follow_up →   │   ├── Self-equipping resolver
    │  demo_site →   │   ├── Safety vetting gate
    │  close →       │   └── DroidClaw / SwarmClaw / ClawdTalk
    │  build →       │
    │  deploy →      │
    │  invoice       │
    └────────────────┘
```

## Port Table

| Service | Port | Type |
|---------|------|------|
| War Room (React) | 3000 | Native (Next.js) |
| API Backend | 8500 | Native (FastAPI/uvicorn) |
| Postgres | 5432 | Docker |
| Qdrant | 6333 | Docker |
| Mem0 | 8888 | Docker |
| N8N | 5678 | Docker |
| Ollama | 11434 | Native |

Official Hermes Agent runs as its own launchd-managed gateway service. `make start` manages the local workers plus the dashboard backend/frontend.

## Local Processes (managed by `make start`)

| System | Process | Restarts on crash |
|--------|---------|-------------------|
| Perseus | `python -m perseus.daemon` | LaunchAgent |
| Titan | `python -m titan.daemon` | LaunchAgent |
| ClawdBot | `python -m clawdbot.daemon` | LaunchAgent |
| Dashboard API | `python -m uvicorn hermes.web.app:app` | LaunchAgent |
| War Room | `pnpm start --port 3000` | LaunchAgent |

## Official Hermes

| System | Process | Restarts on crash |
|--------|---------|-------------------|
| Hermes Agent Gateway | `hermes gateway start` | Hermes launchd service |

## Data Flow

1. **Perseus** reads pipeline state, decides priorities, inserts tasks into `task_queue`
2. **Titan** polls `task_queue`, claims tasks, executes pipeline stages
3. Pipeline stages read/write leads in `clients` table, track status via state machine
4. **Titan** emits events to `events` table when things happen
5. **Dashboard API** exposes local health, pipeline, event, and operator endpoints on `:8500`
6. **Official Hermes Agent** uses synced Perseus skills plus local APIs to monitor and operate the system
7. **War Room** (React + FastAPI) serves the live dashboard on phone
8. **ClawdBot** self-equips from registries, vets skills for safety, handles browser/scraping tasks
9. **Review queue** holds items for Nico's approval (first 10 sales)
10. **titan_rules** table stores data-proven behavioral constraints
11. **agent_decisions** table logs every autonomous decision for auditability

## LLM Routing

| Task | Model | Why |
|------|-------|-----|
| Email composition | Ollama Qwen2.5 14B | Free, local, default fast path |
| High-score lead emails | Claude Sonnet | Best quality for leads scoring 80+ |
| Proposals, strategy | Claude Sonnet | Best quality for important decisions |
| Classification, extraction | Ollama Llama 3.2 3B | Free, local, fast |
| Embeddings | Ollama nomic-embed-text | Free, local |

## Memory Architecture

- **Postgres** — Structured data (leads, deals, metrics, tasks, events, rules, decisions)
- **Qdrant** — Vector search (semantic memory, lead similarity)
- **Mem0** — Agent memory (conversation context, learned insights)
- **titan_learnings** — Structured insights from daily/weekly reflection
- **titan_rules** — Data-proven rules injected into prompts (closed-loop learning)
- **agent_decisions** — Auditable trail of autonomous decisions across all agents
