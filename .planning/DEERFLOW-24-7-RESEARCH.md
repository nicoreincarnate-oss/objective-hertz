# DeerFlow 24/7 Research Loop

## Purpose

Run a dedicated DeerFlow-style research daemon that continuously answers:

- What new Stanford AI work matters to Perseus?
- What new arXiv work matters to Perseus?
- What new or trending GitHub repos should Perseus steal from?
- What should Hermes, Titan, ClawdBot, OpenJarvis, and local inference adopt next?

## Operating Model

The daemon should stay busy at all times:

1. Scan for new external material.
2. If new material exists, classify and queue it for deeper review.
3. If no new material exists, recurse into unresolved research questions and open loops.
4. Merge new findings into the standing "how Perseus should evolve" narrative.
5. Produce a daily brief summarizing 24 hours of research and recommended next actions.

## Source Buckets

- Stanford: HAI, SAIL, Stanford NLP, CS25, project pages, lab posts
- arXiv: cs.AI, cs.CL, cs.LG, cs.SE, agent systems, browsing, memory, local inference
- GitHub: trending, new releases, fast-growing repos, agent tools, MCP tools, browser automation, memory systems

## Output Contract

Every cycle should produce:

- a markdown artifact describing the research cycle
- the source buckets and objective list
- the recommended inference lane for synthesis
- ranked candidates for adopt / test / ignore

Every day should produce:

- a daily evolution brief
- the best donor opportunities
- concrete handoff suggestions for OpenJarvis, Ruflo, ClawdBot, Titan, and Hermes

## Inference Policy

- Default heavy synthesis lane: `local-heavy`
- Preferred heavy backend: `AirLLM`
- Preferred huge-context backend when needed: `oLLM`
- Live operator-facing turns remain on the smart / cloud lane

## Current Implementation Slice

The first live slice adds:

- `deerflow_research` daemon
- A2A surface for `evolution_research_cycle`, `paper_scan`, `repo_scan`, and `daily_evolution_brief`
- scheduler hooks
- routing hooks
- sidecar health/startup hooks
- real ingestors for:
  - arXiv metadata API
  - GitHub search + trending
  - Stanford HAI / SAIL source harvesting

The next slice should add:

- real external fetchers
- source dedupe and freshness tracking
- adoption scoring
- automatic task handoff into implementation queues
