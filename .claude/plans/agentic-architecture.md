# Plan: Make Perseus Truly Agentic

## What this changes

Transform Perseus from a fixed-interval cron scheduler into a system where agents think, adapt, communicate, and self-heal. Covers gaps 1, 2, 3, 4, 13, 16 from the morning audit.

## Phase 1: Perseus becomes a strategic allocator (Gap 3)

**File: `perseus/daemon.py`** — Replace the dumb interval tick with a pipeline-aware decision loop.

Instead of "is lead_discovery due? insert task" Perseus will:
1. Query pipeline state: how many leads at each stage, what's blocked, what's hot
2. Call the LLM (local/fast — free) with the pipeline snapshot + recent learnings
3. Get back a prioritized task list: "3 interested leads → schedule close_interested NOW, skip discovery this cycle"
4. Only schedule what the LLM decides matters

New function: `_assess_pipeline_state()` → returns dict with counts per stage, blocked items, hot leads, recent errors, deliverability metrics.

New function: `_decide_priorities(state)` → LLM call that returns ordered list of tasks to schedule this cycle with reasoning.

The fixed schedules in `scheduler.py` become **maximums** (discovery can't run more than every 30min) but Perseus can skip or reprioritize within those bounds.

**File: `perseus/scheduler.py`** — Add `min_interval_seconds` (floor) and `max_interval_seconds` (ceiling) to each Schedule. Perseus decides when within that range.

## Phase 2: Closed-loop learning (Gaps 2, 13)

**File: `titan/memory.py`** — New concept: **actionable rules** derived from data.

New table: `titan_rules` — structured rules that deterministically change behavior.
```sql
CREATE TABLE titan_rules (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    rule_text TEXT NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_before FLOAT,
    metric_after FLOAT,
    sample_size INTEGER DEFAULT 0,
    confidence FLOAT DEFAULT 0.0,
    active BOOLEAN DEFAULT TRUE,
    source_learning_id INTEGER REFERENCES titan_learnings(id),
    created_at TIMESTAMP DEFAULT NOW(),
    evaluated_at TIMESTAMP
);
```

Flow:
1. Daily reflection already measures metrics (open rate, reply rate, etc.)
2. NEW: After reflection, compare this week's metrics to last week's
3. If a metric improved >10% with sample size >50, extract the **cause** from learnings
4. Store it as a **rule** in `titan_rules` (e.g. "subject lines under 6 words get 2.1x more opens")
5. Pipeline stages read active rules and inject them as **hard constraints** in prompts, not suggestions

**File: `titan/pipeline/email_compose.py`** — Before composing, fetch active rules for category `email_performance` and `copywriting`. Inject them as numbered constraints: "RULE 1: Subject must be under 6 words (proven 2.1x open rate improvement)."

**File: `titan/pipeline/lead_discovery.py`** — Fetch active rules for category `targeting`. If a rule says "restaurants convert at 12%, dentists at 3%", weight search queries accordingly.

**File: `titan/memory.py`** — New function: `evaluate_rules()` called weekly. For each active rule, check if the metric it's supposed to improve has actually improved since the rule was activated. Deactivate rules that don't hold up.

## Phase 3: Agent communication (Gap 1)

**File: `shared/comms.py`** — New primitive: **agent_decisions** table.

```sql
CREATE TABLE agent_decisions (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(100) NOT NULL,
    decision_type VARCHAR(100) NOT NULL,
    context JSONB NOT NULL DEFAULT '{}',
    decision JSONB NOT NULL DEFAULT '{}',
    reasoning TEXT,
    outcome JSONB DEFAULT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

When Perseus decides to prioritize closing over discovery, it writes a decision record. When Titan acts on it, it reads the decision. When the outcome is known, Titan updates the record. This creates an auditable decision trail that feeds back into learning.

**File: `titan/daemon.py`** — Before running the pipeline cycle, Titan reads recent Perseus decisions and adjusts its behavior. If Perseus says "focus on closing", Titan runs close_interested first and gives it more batch capacity.

**File: `shared/comms.py`** — New function: `request_help(from_agent, problem, context)` — an agent can ask another for help. ClawdBot can tell Perseus "I can't scrape this site, need a different approach." Perseus can ask Titan "what's blocking the pipeline?"

## Phase 4: Self-healing infrastructure (Gap 16)

**File: `perseus/health.py`** (NEW) — Infrastructure health monitor.

Checks every 60 seconds:
- **Postgres**: already connected (pool health)
- **Ollama**: `GET /api/tags` — if fails, try `ollama serve` restart
- **Mem0**: `GET /v1/memories/search/` with dummy query — if fails, `docker restart mem0`
- **Instantly API**: lightweight ping (list campaigns with limit=1)
- **Domain health**: read `v_domain_health`, if any domain is CRITICAL → pause sending for that domain

If a dependency fails:
1. Attempt auto-recovery (restart container/service)
2. Wait 10s, recheck
3. If still down: emit `urgent_alert` to Nico, mark dependency as degraded
4. Agents check dependency status before using them — graceful degradation instead of crash

**File: `perseus/daemon.py`** — Call `health.check_infrastructure()` every tick. Store results in `system_config` under `infra_health` so all agents can read it.

**File: `titan/daemon.py`** — Before each pipeline stage, check `infra_health`. If Ollama is down, skip LLM-dependent stages. If Instantly is down, skip email_send. Don't crash — just skip and retry next cycle.

## Phase 5: ClawdBot becomes autonomous (Gap 4)

**File: `clawdbot/daemon.py`** — Add a **think loop** alongside the task queue.

Current: ClawdBot only responds to tasks from the queue.
New: ClawdBot also has a `_think()` function that runs every 5 minutes:
1. Read recent `pipeline_error` events — are there problems I can solve?
2. Read `revenue_expansion_opportunities` with status `proposed` — can I build this?
3. Check `agent_decisions` where another agent asked for help
4. Use the LLM to decide what to do, then do it

For problem-solving: ClawdBot gets access to browser automation (playwright/puppeteer), can sign up for services, install skills, and attempt to fix issues autonomously. When it takes an action, it logs an `agent_decision` record.

**File: `clawdbot/browser.py`** (NEW) — Headless browser wrapper using playwright. Methods: `navigate(url)`, `screenshot()`, `fill_form(selector, value)`, `click(selector)`, `get_text(selector)`, `scroll_page()`.

**File: `clawdbot/daemon.py`** — New task type: `verify_demo_site` — opens demo URL in headless browser, scrolls through, checks content matches business name/industry, takes screenshot, returns pass/fail. Wired into `close_deal.py` before sending proposals.

## Phase 6: Reference-driven site generation

**Goal:** upgrade the existing website build path, not replace it. Titan research should hand ClawdBot structured inspiration inputs, and ClawdBot should synthesize original demo/full sites using installed design skills plus external design references.

**File: `titan/pipeline/lead_research.py`** — Add a compact design handoff inside existing `research_facts` JSON:
- `reference_sites`
- `reference_patterns`
- `anti_patterns`
- `design_positioning`

Research keeps runtime truth local: candidate references come from live search results already gathered during enrichment. The LLM turns those into design patterns and anti-patterns, but does not invent copied layouts.

**File: `clawdbot/site_builder.py`** — Add a pre-build strategy phase before variant generation:
1. Read `research_facts` from the lead
2. Mine inspiration URLs and design packet fields
3. Invoke installed design skills, especially `ui-ux-pro-max`, for visual direction and UX guidance
4. Generate an original strategy brief that explicitly uses:
   - `shadcn/ui` as the production component spine
   - `21st.dev` as selective block/pattern inspiration
   - `Stitch` as visual direction/prototyping influence
5. Feed that strategy into the existing variant → review → synthesis → deploy loop

Guardrail: no exact HTML, assets, branding, or layout cloning from references or community blocks.

## Files to modify
- `perseus/daemon.py` — Strategic allocation loop
- `perseus/scheduler.py` — Min/max intervals
- `perseus/health.py` (NEW) — Infrastructure health monitor
- `titan/daemon.py` — Read Perseus decisions, check infra health
- `titan/memory.py` — Rules system, rule evaluation
- `titan/pipeline/email_compose.py` — Inject active rules
- `titan/pipeline/lead_discovery.py` — Inject targeting rules
- `shared/comms.py` — Agent decisions, help requests
- `clawdbot/daemon.py` — Think loop, demo verification
- `clawdbot/browser.py` (NEW) — Headless browser wrapper
- `clawdbot/site_builder.py` — Reference-driven, skill-aware site strategy
- `titan/pipeline/lead_research.py` — Structured design handoff in `research_facts`
- `scripts/init-db.sql` — New tables (titan_rules, agent_decisions)
- `scripts/migrations/002-agentic-tables.sql` (NEW) — Migration for existing installs

## Order of implementation
1. Schema changes (tables first)
2. Perseus strategic allocator (biggest impact — agents start thinking)
3. Self-healing health monitor (safety net before going autonomous)
4. Closed-loop rules system (learnings become actions)
5. Agent communication (decisions + help requests)
6. ClawdBot think loop + browser (autonomous problem-solving)

## What this does NOT change
- Compliance gate — untouched, already A+
- Review mode — untouched
- Budget enforcement — untouched
- State machine — untouched
- Email send pipeline — untouched except rule injection
- Payment routing — untouched

## Revenue-critical gaps left for Phase 2 plan
- Gap 5: Source quality tracking
- Gap 6: Dynamic pricing
- Gap 7: Unit economics
- Gap 8: Email gate on leads without email
- Gap 9: Deliverability monitoring
- Gap 10: Autonomous domain protection
- Gap 11: Instantly sequence conflict
- Gap 20: Multilingual unsubscribe detection

These are smaller, targeted fixes that can be done after the agentic architecture is in place.
