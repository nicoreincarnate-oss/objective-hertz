---
description: Audit context window usage and identify token waste
---

# Context Audit

## Purpose
Analyze what's consuming context and recommend optimizations.

## Instructions
1. Run `/context` to see current token consumption
2. Identify the top consumers:
   - MCP servers loaded (10K+ tokens each)
   - Large files read into context
   - Accumulated conversation history
   - Skills/tools loaded
3. For each MCP server:
   - Is it actively being used?
   - Can it be replaced with a CLI-teaching prompt?
   - Can it be replaced with a skill?
4. Check CLAUDE.md size — is it bloated?
5. Check if context reset is needed

## Output
```
CONTEXT AUDIT
=============
Total usage:     [estimate]
MCP servers:     [N loaded, X tokens each]
Conversation:    [length estimate]
Files in context: [list]

RECOMMENDATIONS:
- [Replace MCP X with CLI prompt → save ~10K tokens]
- [Reset context after current phase]
- [Move section Y from CLAUDE.md to skill]
```

## Rules
- Recommend MCP replacement only if a CLI alternative exists
- Don't remove MCP servers that are actively needed
- Always suggest the least disruptive optimization first
