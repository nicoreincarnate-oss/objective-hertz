# DeerFlow 2.0 (ByteDance) — Reference

**Repository:** https://github.com/bytedance/deer-flow
**License:** MIT
**Stars:** 37k+ (Hit #1 GitHub Trending Feb 28, 2026)
**Stack:** LangGraph + LangChain, Python, Docker

---

## Why This Matters for Objective Hertz

OH's 5-daemon architecture (Perseus, Titan, Hermes, ClawdBot, Conway) communicating via A2A + Postgres is architecturally more sophisticated than DeerFlow. But DeerFlow has production-tested patterns for three things OH needs: persistent memory across restarts, sandboxed execution per agent, and skill hot-loading. These patterns can be ported without migrating frameworks.

---

## Core Architecture

### System Components
- **Lead Agent** — Entry point, manages task orchestration via LangGraph
- **Sub-agents** — General-purpose and bash specialist, spawned via `task()` tool
- **Sandbox** — Isolated execution (Local, Docker, or Kubernetes)
- **Gateway API** — FastAPI on port 8001
- **LangGraph Server** — Port 2024
- **Frontend** — React on port 3000

### Multi-Agent Orchestration
- Lead agent routes tasks and spawns sub-agents via `task()` tool
- **Dual thread pool** for scheduling/execution (3 workers each)
- **MAX_CONCURRENT_SUBAGENTS = 3** enforced
- **15-minute timeout** per task execution
- SSE event streaming: task_started → task_running → task_completed/failed/timed_out

---

## Sandbox Architecture (Key Pattern to Steal)

### Abstract Interface
```python
class SandboxProvider:
    execute_command(command) → stdout, stderr, exit_code
    read_file(path) → content
    write_file(path, content) → success
    list_dir(path) → entries
```

### Virtual Path Mapping
Agents see a virtualized filesystem:
```
/mnt/user-data/workspace/    → Agent's working directory
/mnt/user-data/uploads/      → User-uploaded files
/mnt/user-data/outputs/      → Agent outputs
/mnt/skills/                  → Available skill definitions
/mnt/acp-workspace/           → Read-only shared workspace
```

### Provider Pattern (Pluggable)
1. **LocalSandboxProvider** — Filesystem with path mapping (development)
2. **AioSandboxProvider** — Docker isolation (production)
3. **Kubernetes** — Pod-provisioned (scale)

### OH Integration Point
Currently OH's daemons share the same filesystem. When Baja Swarm scales to 17+ concurrent WhatsApp instances or OH runs multiple Titan pipeline cycles simultaneously, DeerFlow's Docker-per-agent pattern prevents one daemon's failure from cascading.

---

## Memory System (Key Pattern to Steal)

### Automatic Extraction
- LLM analyzes conversations for user context and facts
- **No manual memory tagging required** — system extracts automatically

### Storage Format: `memory.json`
```json
{
  "userContext": {
    "workContext": "...",
    "personalContext": "...",
    "topOfMind": "..."
  },
  "history": {
    "recentMonths": "...",
    "earlierContext": "...",
    "longTermBackground": "..."
  },
  "facts": [
    {
      "id": "uuid",
      "content": "User prefers restaurants as first niche",
      "category": "preference",
      "confidence": 0.9,
      "createdAt": "2026-03-29T00:00:00Z",
      "source": "conversation"
    }
  ]
}
```

### Memory Injection Pattern
- **Top 15 facts** queued into system prompt (30-second debounce)
- **Deduplication** via whitespace-normalized fact matching
- **Three time layers**: recent months, earlier context, long-term background

### OH Integration Point
Perseus restarts from Postgres state but loses planning context. DeerFlow's pattern: on daemon startup, load memory.json and inject recent context + top facts into the daemon's system prompt. This means Perseus remembers "I was about to run lead discovery on restaurants in Portland" instead of re-planning from scratch.

---

## Skill Modules (Validates OH's ClawdBot Pattern)

### Directory Structure
```
deer-flow/skills/
├── public/              # Built-in skills
│   ├── web-research/
│   │   └── SKILL.md
│   └── code-execution/
│       └── SKILL.md
└── custom/              # User-installed skills
    └── my-skill/
        └── SKILL.md
```

### Skill Format
```yaml
# SKILL.md frontmatter
---
name: web-research
description: Research topics using web search
license: MIT
allowed-tools:
  - tavily
  - jina_ai
---

# Skill instructions follow...
```

### Dynamic Loading
- Recursive directory scan
- Metadata parsed from YAML frontmatter
- Enable/disable state from `extensions_config.json`
- Enabled skills injected into agent system prompt

### Installation API
```
POST /api/skills/install
# Accepts .skill ZIP archives, extracts to custom/
```

### OH Integration Point
ClawdBot already has `skill_loader.py` with 4 registry directories and `capability_resolver.py` for auto-install. DeerFlow validates this approach. The one thing DeerFlow does better: explicit `allowed-tools` in skill frontmatter, constraining which tools a skill can access. ClawdBot's `safety.py` does security vetting but doesn't restrict tool access per-skill.

---

## Middleware Chain (12 Middlewares, Sequential)

Worth studying for OH's pipeline validation:

1. **ThreadDataMiddleware** — Per-thread working directories
2. **UploadsMiddleware** — File tracking
3. **SandboxMiddleware** — Sandbox acquisition
4. **DanglingToolCallMiddleware** — Placeholder injection for incomplete calls
5. **GuardrailMiddleware** — Pre-tool-call authorization (pluggable provider)
6. **SummarizationMiddleware** — Token limit reduction via summarization
7. **TodoListMiddleware** — Plan mode task tracking
8. **TitleMiddleware** — Auto-generated titles
9. **MemoryMiddleware** — Async memory extraction queue
10. **ViewImageMiddleware** — Vision model image injection
11. **SubagentLimitMiddleware** — Concurrency enforcement
12. **ClarificationMiddleware** — Flow interruption for user input

### OH Integration Point
Titan's pipeline stages are sequential but don't have a middleware pattern for cross-cutting concerns. Adding guardrails (anti-slop, compliance, budget check) as middleware rather than per-stage code would be cleaner and harder to bypass.

---

## Tool System

### Assembly Pattern
`get_available_tools()` builds tool list from:
1. Config-defined tools
2. MCP tools (lazy-loaded with mtime cache invalidation)
3. Built-in tools: present_files, ask_clarification, view_image
4. Subagent tool (conditional task delegation)
5. Community tools: tavily, jina_ai, firecrawl, image_search

### MCP Integration
- **MultiServerMCPClient** from langchain-mcp-adapters
- Lazy initialization on first use
- Cache invalidation via mtime detection
- OAuth support for HTTP/SSE transports
- Runtime updates propagate without restart

---

## Configuration (Auto-Reload)

### config.yaml
```yaml
models:
  - name: claude-sonnet
    thinking: true
    vision: true
tools:
  - tavily
  - firecrawl
sandbox:
  provider: docker
skills:
  enabled: true
memory:
  enabled: true
  max_facts: 15
```

- **Mtime-based detection** — Config changes apply without restart
- **`config_version`** field signals schema updates
- **Model factory** — `create_chat_model()` with reflection-based instantiation

---

## Key Architectural Boundaries

```
packages/harness/deerflow/    → Publishable framework (the "SDK")
app/                          → Application layer (FastAPI, IM integrations)

Rule: App imports Harness, never vice-versa (CI enforcement)
```

This separation is worth studying for OH. Currently `openjarvis/` is the framework and the daemons are the app layer, but the boundary isn't CI-enforced.

---

## Sources

- [DeerFlow GitHub](https://github.com/bytedance/deer-flow)
- [DeerFlow CLAUDE.md](https://github.com/bytedance/deer-flow/blob/main/backend/CLAUDE.md)
- [DeerFlow Installer](https://github.com/bytedance-deerflow/deer-flow-installer)
- [Medium Deep Dive](https://agentnativedev.medium.com/deerflow-2-0-open-source-superagent-harness-88d68c4d09ee)
- [DeepWiki Technical Analysis](https://deepwiki.com/bytedance/deer-flow)
- [MarkTechPost Coverage](https://www.marktechpost.com/2026/03/09/bytedance-releases-deerflow-2-0-an-open-source-superagent-harness-that-orchestrates-sub-agents-memory-and-sandboxes-to-do-complex-tasks/)
