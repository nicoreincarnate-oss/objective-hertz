# Agent DNA Auditor Pattern — Reference

**Concept Source:** Agentic engineering community practice (2025-2026)
**Related Repos:**
- https://github.com/DataWhisker/anti-slop-skill (quality DNA)
- https://github.com/jalaalrd/anti-ai-slop-writing (writing DNA)
- DeerFlow CLAUDE.md pattern (agent instruction DNA)

---

## Why This Matters for Objective Hertz

OH has 5 daemons (Perseus, Titan, Hermes, ClawdBot, Conway) plus Ruflo (wired, inactive). Each operates with its own logic but there's no shared "DNA" layer — universal engineering principles that every daemon inherits regardless of its specialty. The `soul/` directory has personality and copywriting guidelines but not engineering DNA. When you add a new daemon or skill, it doesn't automatically know to check budget_guard, run compliance gates, or quality-check outputs.

**Direct application:** Create a DNA layer in `soul/` that every daemon loads on startup. Engineering principles that are true whether the daemon is discovering leads, composing emails, building sites, or managing wallets.

---

## The Two-Layer Architecture

### Layer 1: DNA (Immutable, Never Changes Per Project)

DNA is how an agent **thinks**. Methodology, ideology, best practices that apply regardless of what project it's working on. A senior backend engineer doesn't forget clean architecture when switching from Python to TypeScript — that knowledge is part of who they are.

**Examples:**
- Clean architecture and bounded context
- API-contract-first design
- Security by default (no hardcoded secrets, input validation)
- Compliance gates on all outward-facing actions
- Budget enforcement before any spend
- Quality gates on all generated content
- Logging every decision for audit trail
- Error handling that surfaces, not swallows
- Testing before deployment
- Graceful degradation over hard failure

### Layer 2: Skills (Invoked, Changes Per Project/Context)

Skills are what specific technology the project uses. They're loaded dynamically based on the current task.

**Examples:**
- Instantly.ai API patterns (Titan email)
- Stripe payment integration (Conway/payment_router)
- Netlify deployment (ClawdBot)
- WhatsApp Evolution API (Baja Swarm)
- kyegomez/swarms orchestration (Baja Swarm)
- Recraft image generation (ClawdBot)
- Postgres query patterns (shared/db.py)

---

## Implementation for Objective Hertz

### DNA File: `soul/engineering_dna.md`

```markdown
# Engineering DNA — Universal Principles

Every daemon, agent, and skill in Objective Hertz MUST follow these principles.
They are non-negotiable and do not change per task.

## Security
- Never hardcode API keys, tokens, or secrets
- Validate all external input before processing
- Use parameterized queries (never string concatenation for SQL)
- Log all external API calls with timestamps
- Sanitize all data before storing in database

## Compliance
- Every outward-facing action passes compliance gate
- CAN-SPAM on all emails (unsubscribe link, physical address)
- LFPDPPP on all WhatsApp outreach (Baja Swarm)
- Suppression lists checked before every contact
- Full audit trail for every prospect interaction

## Budget
- Check budget_guard BEFORE any paid API call
- Track cost per action in budget_tracking table
- Degrade to free alternatives (Ollama) when budget stressed
- Never exceed $800/month cap without human approval

## Quality
- All generated content (emails, site copy, DMs) passes anti-slop check
- Review with fresh context (no composition bias)
- No hedging language, no corporate buzzwords, no AI tells
- Specific > generic. If it could be sent to anyone, it's slop.

## Resilience
- Every daemon handles restart gracefully (load state from DB)
- No silent failures — log errors, alert via Hermes
- Graceful degradation: if a service is down, skip that stage, don't crash
- Health checks every 30 seconds via Perseus

## Decisions
- Log every autonomous decision to agent_decisions table
- Include: what was decided, why, what alternatives were considered
- First 10 sales require human approval (review_mode)
- Recurring cost >$50/mo or one-time >$200 requires human approval

## Learning
- Extract learnings from every pipeline cycle to titan_learnings
- Daily reflection: what worked, what didn't, what to try differently
- Weekly consolidation: patterns → rules → updated behavior
- Never overwrite learnings — version them
```

### How Daemons Load DNA

```python
# In shared/config.py or a new shared/dna.py

import pathlib

DNA_PATH = pathlib.Path("soul/engineering_dna.md")

def load_dna() -> str:
    """Load engineering DNA for inclusion in any agent's system prompt."""
    if DNA_PATH.exists():
        return DNA_PATH.read_text()
    return ""

# In each daemon's initialization:
# titan/daemon.py, perseus/daemon.py, hermes/daemon.py, clawdbot/daemon.py

from shared.dna import load_dna

class TitanDaemon:
    def __init__(self):
        self.dna = load_dna()
        # Include self.dna in every LLM call's system prompt
```

### DNA for Sub-Agents and Skills

When ClawdBot loads a skill or Titan spawns a sub-task:
```python
# In shared/llm_client.py

async def call_llm(prompt, system_prompt=None, include_dna=True):
    if include_dna and system_prompt:
        system_prompt = load_dna() + "\n\n" + system_prompt
    elif include_dna:
        system_prompt = load_dna()
    # ... rest of LLM call
```

This ensures every LLM call in the system — whether it's Titan composing an email, ClawdBot building a site, or Hermes generating a morning briefing — inherits the DNA.

---

## Cross-Project DNA (Baja Swarm + OH + Trading)

### Global DNA Layer
```
~/Documents/Claude/.global-dna/
├── ENGINEERING_DNA.md      # Universal principles
├── SECURITY_DNA.md         # Security practices
├── QUALITY_DNA.md          # Anti-slop, quality gates
└── COMPLIANCE_DNA.md       # Legal/regulatory principles
```

Each project's CLAUDE.md starts with:
```markdown
# Load Global DNA
@import ~/.global-dna/ENGINEERING_DNA.md
@import ~/.global-dna/SECURITY_DNA.md

# Project-Specific Configuration
... (the rest of CLAUDE.md)
```

### Why This Matters
When you audit your workflow weekly, you update DNA once. If you discover a new anti-slop pattern, add it to `QUALITY_DNA.md` and every project inherits it. Currently you'd have to update OH's CLAUDE.md, Baja Swarm's CLAUDE.md, and Trading's CLAUDE.md separately.

---

## Auditing DNA Coverage

### DNA Audit Checklist
For each daemon/agent, verify:

- [ ] Loads engineering DNA on startup
- [ ] Includes DNA in every LLM system prompt
- [ ] Compliance gates active on outward-facing actions
- [ ] Budget guard checked before paid API calls
- [ ] Quality gate on generated content
- [ ] Decisions logged to agent_decisions
- [ ] Error handling surfaces (not swallows)
- [ ] Health check responds correctly
- [ ] Restart recovers state from DB

### When to Run
- Weekly during your workflow audit
- Before adding any new daemon or skill
- After any major refactor

---

## Anti-Pattern: What DNA Is NOT

DNA is NOT:
- Project-specific configuration (API keys, endpoints, model choices)
- Task-specific instructions (how to compose an email, how to build a site)
- Runtime state (current pipeline status, active leads)
- Skills (Instantly.ai patterns, Stripe integration)

DNA IS:
- Engineering principles (security, quality, resilience)
- Behavioral constraints (logging, compliance, budget)
- Methodology (test before deploy, graceful degradation, decision audit trail)

If it would change when you switch projects, it's a Skill, not DNA.
If it's true regardless of what you're building, it's DNA.
