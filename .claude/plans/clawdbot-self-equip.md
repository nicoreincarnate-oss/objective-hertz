# Plan: ClawdBot Self-Equipping Ability

## What ClawdBot should be

An autonomous problem-solver that can identify what tools it's missing, go find them, install them, and use them — without asking Nico. When it sees "I need a browser to QA this demo site" and no browser skill is installed, it should go get one.

## Current state

- `clawdbot/daemon.py` handles tasks: `skill_execute`, `web_scrape`, `verify_single_site`, `browser_task`, `enrich_lead`, `verify_demo_site`
- Think loop checks `events` and `revenue_expansion_opportunities` every cycle
- When a needed skill is missing, it logs `expansion_blocked` and stops
- `shared/skill_loader.py` has `find_skill()` and `execute_skill()` — skills are markdown SKILL.md files in known directories
- The expansion system can propose new capabilities but delegates building to ClawdBot via `skill_execute` with a builder skill (codex-collab, claude-code, openclaw-coder)

## Changes

### 1. Capability resolver in ClawdBot (`clawdbot/capability_resolver.py`)

New module. When ClawdBot needs a capability it doesn't have:

```
async def resolve_capability(need: str, context: dict) -> dict
```

Steps:
1. **Check installed skills** — `find_skill()` across all known skill names for the need (browser, scraper, email-finder, etc.)
2. **Search skill directories** — scan `~/.claude/skills/`, `~/.claude/commands/`, project `.claude/commands/` for partial matches
3. **Try to install via package manager** — if the need is a known Python package (playwright, browser-use, etc.), run `pip install` and record in `agent_decisions`
4. **Ask a builder skill** — if a builder skill exists (claude-code, openclaw-coder), create a task to build a minimal skill wrapper
5. **Fall back gracefully** — if nothing works, emit `capability_missing` event with what was needed and why, so Hermes alerts Nico

Each step records a decision in `agent_decisions` for auditability.

Returns: `{"resolved": bool, "method": str, "skill_name": str, "details": str}`

### 2. Wire capability resolver into task handlers (`clawdbot/daemon.py`)

When a task handler fails because a tool is missing (ImportError, skill not found, etc.):
- Instead of just logging and failing, call `resolve_capability()`
- If resolved, retry the task once
- If not resolved, fail the task with a clear error and emit alert

Modify these handlers:
- `handle_browser_task` — resolve "browser" capability
- `handle_site_verify` / `handle_site_verify_batch` — resolve "browser" or fall back to HTTP
- `verify_demo_site` — resolve "browser" for full QA, fall back to HTTP checks

### 3. Known capability map (`clawdbot/capabilities.py`)

Static mapping of capability needs to resolution strategies:

```python
CAPABILITY_MAP = {
    "browser": {
        "skills": ["browser-use", "playwright-browser", "puppeteer"],
        "packages": ["playwright", "browser-use"],
        "post_install": "playwright install chromium",
    },
    "email_finder": {
        "skills": ["email-finder", "hunter-io", "snov-io"],
        "packages": [],
    },
    "phone": {
        "skills": ["twilio-sms", "voip-caller"],
        "packages": ["twilio"],
    },
    "code_builder": {
        "skills": ["claude-code", "codex-collab", "openclaw-coder"],
        "packages": [],
    },
}
```

This is the "what do I need and where can I find it" knowledge. ClawdBot reads this before trying to resolve. New entries can be added by the expansion system.

### 4. Self-install tracking in system_config

When ClawdBot installs something, record it:
- `installed_capabilities` — JSON list of `{capability, method, installed_at, skill_or_package}`
- This prevents re-installing on every cycle and lets other agents know what's available

### 5. Think loop enhancement (`clawdbot/daemon.py` think loop)

Current think loop checks events and expansion opportunities. Add:
- Check `capability_missing` events from the last hour — if the same capability was requested 3+ times, proactively try to resolve it
- Check `agent_help_request` events — if another agent asked for help with something ClawdBot can resolve, claim it

### 6. Website design capability extension

ClawdBot's capability map should explicitly include design/build capabilities for the existing site pipeline:
- `design_reference_mining`
- `ui_skill_invocation`
- `shadcn_component_assembly`
- `stitch_direction_generation`
- `21st_block_adaptation`

These do not create a second website system. They feed the current `site_builder` flow.

Rules:
- Preserve installed design skills, especially `ui-ux-pro-max`, as first-class inputs
- Treat `21st.dev` as selective block inspiration, not a copy source
- Treat `Stitch` as direction/prototyping guidance, not runtime truth
- Keep Python orchestration-only; AI still chooses direction, synthesis, and tradeoffs

## Files to create
- `clawdbot/capability_resolver.py` — the resolver logic
- `clawdbot/capabilities.py` — static capability map

## Files to modify
- `clawdbot/daemon.py` — wire resolver into task handlers and think loop
- `clawdbot/site_builder.py` — consume design capabilities inside the existing build path

## What this does NOT do
- No auto-downloading random packages from the internet without the capability map
- No self-modifying code — ClawdBot installs skills/packages, it doesn't edit its own source
- No spending money — pip installs are free, skill searches are free, builder skills use the existing LLM budget

## Verification
```bash
python3 -m py_compile clawdbot/capability_resolver.py clawdbot/capabilities.py clawdbot/daemon.py
python3 -m pytest tests/ -v
```
