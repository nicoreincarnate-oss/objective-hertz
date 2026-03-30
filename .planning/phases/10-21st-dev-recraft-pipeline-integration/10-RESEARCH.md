# Phase 10: 21st.dev + Recraft Pipeline Integration - Research

**Researched:** 2026-03-30
**Domain:** ClawdBot site builder pipeline -- 21st.dev component integration + Recraft image generation wiring
**Confidence:** HIGH

## Summary

The ClawdBot site builder currently references "21st.dev-style" patterns as text-only prompt guidance in 6 locations across `site_builder.py`. No actual component code is ever fetched from 21st.dev. Similarly, the Recraft client (`tools/recraft_client.py`) is fully implemented and wired into `_generate_asset_pack()` via `handle_image_generation()` in `daemon.py`, but the pipeline flow is gated by budget guards and was never exercised in the test run (budget guard returned `allow_paid_assets=False`).

The 21st.dev Magic MCP server exposes 4 tools via the MCP protocol, with the key one being `21st_magic_component_inspiration` which calls `POST https://magic.21st.dev/api/fetch-ui` with a search query and returns component code snippets. The approach for integration should NOT use MCP protocol (too complex for this use case) -- instead, call the 21st.dev REST API directly from Python using httpx, same pattern as the existing Recraft client.

**Primary recommendation:** Create a `tools/twentyfirst_client.py` that calls the 21st.dev REST API directly (POST to `/api/fetch-ui` with `x-api-key` header). Wire it into `design_sources.py` to replace slug-based text references with actual component code. Wire Recraft asset generation into the pipeline with proper budget tracking. Ensure the full 9-step pipeline runs end-to-end.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TWENTY1-01 | 21st.dev MCP configured and queryable from site builder | REST API at `magic.21st.dev/api/fetch-ui` with `x-api-key` auth; Python httpx client |
| TWENTY1-02 | Real component code injected into variant prompts | `21st_magic_component_inspiration` returns component snippets; inject into brief/variant prompt |
| TWENTY1-03 | Components adapted to business context, not copied verbatim | Existing adaptation_rules in design_sources.py; add post-fetch transformation step |
| RECRAFT-01 | Recraft generates logo + hero images with budget tracking | Already implemented in `tools/recraft_client.py` + `_generate_asset_pack()`; needs budget unblocking |
| RECRAFT-02 | Budget tracking for Recraft API calls | Already wired via `budget_tracking` table in `_generate_asset_pack()`; verify correctness |
| RECRAFT-03 | Generated assets injected into variant HTML | Already wired via `logo_url`/`hero_url` in `_build_one_variant()` prompt |
| PIPELINE-01 | Full pipeline runs: strategy -> assets -> variants -> review -> synthesis -> anti-slop -> deploy | Pipeline exists but only 20% exercised; need to verify/fix review + synthesis steps |
| PIPELINE-02 | Built sites score higher with real components vs text-only references | Add comparison test; site_quality.py already has scoring infrastructure |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | 0.28.1 | HTTP client for 21st.dev REST API | Already used project-wide (recraft_client, etc.) |
| mcp | 1.26.0 | MCP Python SDK (NOT used for 21st.dev calls) | Installed but too complex for simple REST calls |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tools/recraft_client.py | existing | Recraft V3/V4 image generation | Logo + hero generation in asset pack |
| clawdbot/design_sources.py | existing | Curated design source resolution | Wire 21st.dev component fetching here |
| clawdbot/site_quality.py | existing | Site QA scoring | Validate improvement from real components |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct REST API calls to 21st.dev | MCP Python SDK client (spawn subprocess) | MCP adds subprocess management complexity for a simple POST call; REST is simpler |
| httpx POST to magic.21st.dev | npx @21st-dev/magic (Node subprocess) | Unnecessary Node dependency; Python httpx is cleaner |

**No new installations required.** httpx is already a dependency.

## Architecture Patterns

### Integration Points in Pipeline

The site builder pipeline has 9 stages. Here is where each integration goes:

```
1. _generate_reference_strategy()    <-- 21st.dev: fetch components for industry/section type
2. _make_build_plan()                <-- Budget: check Recraft budget allowance
3. _generate_asset_pack()            <-- Recraft: generate logo + hero (ALREADY WIRED)
4. _build_variants() x N             <-- 21st.dev: inject component code into variant prompt
5. Opus review                       <-- No change (reviews variant quality)
6. Synthesis (cherry-pick best)      <-- No change
7. Anti-slop gate                    <-- No change (ALREADY WIRED)
8. Inner pages                       <-- No change
9. Deploy                            <-- No change
```

### Recommended New File Structure
```
tools/
  twentyfirst_client.py   # NEW: 21st.dev REST API client (mirrors recraft_client.py pattern)
clawdbot/
  design_sources.py       # MODIFY: add component code fetching via twentyfirst_client
  site_builder.py         # MODIFY: inject real component code into prompts
```

### Pattern 1: Direct REST API Client (for 21st.dev)
**What:** Call `POST https://magic.21st.dev/api/fetch-ui` directly from Python using httpx
**When to use:** Every time the pipeline needs component inspiration for a section type
**Example:**
```python
# Source: https://github.com/21st-dev/magic-mcp/blob/main/src/tools/fetch-ui.ts
# and https://github.com/21st-dev/magic-mcp/blob/main/src/utils/http-client.ts

import httpx
import os

TWENTYFIRST_API_BASE = "https://magic.21st.dev"

async def fetch_component_inspiration(
    message: str,
    search_query: str,
    timeout: float = 30.0,
) -> dict:
    """Fetch component code from 21st.dev for section inspiration."""
    api_key = os.getenv("TWENTYFIRST_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{TWENTYFIRST_API_BASE}/api/fetch-ui",
            json={"message": message, "searchQuery": search_query},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"text": data.get("text", ""), "search_query": search_query}
```

### Pattern 2: Component Code Injection into Variant Prompt
**What:** Replace text-only "21st.dev-style" references with actual component snippets
**When to use:** In `_build_one_variant()` prompt construction
**Example:**
```python
# Inject fetched component patterns into the variant brief
component_snippets = []
for section_type in ["hero", "testimonials", "services", "cta"]:
    snippet = await fetch_component_inspiration(
        message=f"{section_type} section for {industry} business website",
        search_query=f"{section_type} section",
    )
    if snippet.get("text"):
        component_snippets.append(f"--- {section_type.upper()} PATTERN ---\n{snippet['text'][:2000]}")

# Add to variant prompt as reference patterns (not copy targets)
component_block = "\n\n".join(component_snippets) if component_snippets else ""
```

### Pattern 3: Budget-Gated Asset Generation (already exists)
**What:** Recraft image generation gated by monthly and per-site budget caps
**When to use:** In `_generate_asset_pack()` -- already implemented
**Key code path:**
```
site_builder.py:_generate_asset_pack()
  -> daemon.py:handle_image_generation()
    -> recraft_client.py:generate_logo() / generate_hero_image()
```

### Anti-Patterns to Avoid
- **Spawning MCP subprocess for simple REST calls:** The 21st.dev API is a standard REST endpoint; don't add MCP protocol complexity
- **Fetching components at variant-build time in parallel without caching:** Each variant would make the same API calls; fetch once in strategy phase, reuse
- **Injecting raw component React/TSX code into HTML variant prompts:** 21st.dev returns React components; the site builder generates vanilla HTML+Tailwind -- must adapt
- **Removing budget guards to "make Recraft work":** Budget guards are correct; the issue is configuration, not code

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Component inspiration fetching | Custom scraper for 21st.dev website | `POST /api/fetch-ui` REST endpoint | Official API, maintained, returns structured data |
| Image generation | Custom Stable Diffusion pipeline | Recraft API via `tools/recraft_client.py` | Already built, budget-tracked, $0.04/image |
| Logo SVG search | Manual SVG creation | 21st.dev `logo_search` tool (future) | Returns brand-correct SVGs; defer to later phase |
| Component code adaptation | Manual regex transforms on React code | LLM-based adaptation in prompt | React-to-HTML conversion needs semantic understanding |

**Key insight:** The site builder already generates HTML via LLM prompts. Component code from 21st.dev should be injected as *structural inspiration* in the prompt, not as literal code to include. The LLM will adapt React patterns into vanilla HTML+Tailwind naturally.

## Common Pitfalls

### Pitfall 1: 21st.dev Returns React/TSX, Site Builder Outputs HTML
**What goes wrong:** Injecting raw React component code into a vanilla HTML generation prompt confuses the LLM
**Why it happens:** 21st.dev is a React component library; the site builder generates single-file HTML+Tailwind
**How to avoid:** Frame fetched components as "structural inspiration" and "pattern reference" in the prompt, not as code to copy. The LLM naturally converts React patterns to HTML when given clear instructions.
**Warning signs:** Generated HTML contains JSX syntax (`className=`, `onClick=`, self-closing tags), or the LLM outputs a React component instead of HTML

### Pitfall 2: API Rate Limits on 21st.dev Free Tier
**What goes wrong:** With 5 free requests and 5-7 section types per site build, the free tier is exhausted in one build
**Why it happens:** Free tier is 5 requests total; $20/month plan has higher limits
**How to avoid:** Cache fetched components by search_query in memory/DB for the session. Batch section requests. Set per-site fetch limit (max 4 components). Add TWENTYFIRST_API_KEY to .env.
**Warning signs:** 403 or 429 responses from the API

### Pitfall 3: Budget Guard Blocks All Recraft Calls
**What goes wrong:** `_generate_asset_pack()` returns `{"reason": "budget_guard"}` because `allow_paid_assets=False`
**Why it happens:** `monthly_paid_asset_cap` defaults from settings object which may return 0 or False
**How to avoid:** Verify the settings defaults: `monthly_paid_asset_cap=30.0`, `per_site_asset_cap=0.08` (full) / `0.03` (demo). Ensure RECRAFT_API_KEY is set (already confirmed in .env).
**Warning signs:** All built sites lack logos and hero images despite RECRAFT_API_KEY being set

### Pitfall 4: Component Fetch Failure Blocks Entire Pipeline
**What goes wrong:** 21st.dev API timeout or error prevents site from building
**Why it happens:** No fallback when component fetch fails
**How to avoid:** Make component fetching non-fatal. Fall back to existing text-only references (current behavior) when API is unavailable. Log warning but proceed.
**Warning signs:** Site builds fail with network errors to magic.21st.dev

### Pitfall 5: Prompt Bloat from Large Component Snippets
**What goes wrong:** Injecting full component code (1000+ tokens each) for 5 sections bloats the prompt beyond useful context
**Why it happens:** 21st.dev components can be large; multiplied by 5 sections = 5000+ tokens of reference code
**How to avoid:** Truncate each component snippet to ~500 tokens. Extract only the structural pattern (section layout, key classes, structure) not the full implementation. Summarize with LLM if needed.
**Warning signs:** Variant generation costs spike, or quality degrades due to context dilution

## Code Examples

### Existing: How design_sources.py works today
```python
# Source: clawdbot/design_sources.py (lines 8-41)
# Text-only curated sources with slug-based references
_CURATED_SOURCES = [
    {
        "source": "21st.dev",
        "title": "Split Hero CTA",
        "slug": "split-hero-cta",           # <-- Just a slug, never fetched
        "industries": {"service", "dentist"},
        "sections": ["hero", "proof", "cta"],
        "why": "Strong hero framing with immediate CTA",
    },
    # ...
]
```

### Target: How design_sources.py should work after Phase 10
```python
# design_sources.py with real component fetching
async def resolve_design_sources_with_components(lead: dict) -> dict:
    """Resolve design sources AND fetch actual component code."""
    base = resolve_design_sources(lead)  # existing function
    industry = str(lead.get("industry", "") or "").lower()

    # Fetch real components for matched sources
    enriched_sources = []
    for source in base["sources"]:
        if source.get("source") == "21st.dev":
            sections = source.get("sections", [])
            for section in sections[:2]:  # max 2 fetches per source
                snippet = await fetch_component_inspiration(
                    message=f"{section} section for {industry} business",
                    search_query=f"{section} {source.get('title', '')}",
                )
                if snippet.get("text"):
                    source.setdefault("component_snippets", []).append({
                        "section": section,
                        "code": snippet["text"][:1500],
                    })
        enriched_sources.append(source)

    base["sources"] = enriched_sources
    return base
```

### Existing: How Recraft is already wired
```python
# Source: clawdbot/site_builder.py (lines 599-660)
# Already wired through handle_image_generation -> recraft_client
async def _generate_asset_pack(lead: dict, build_plan: dict) -> dict:
    if not build_plan.get("allow_paid_assets"):
        return {"logo_url": "", "hero_url": "", "reason": "budget_guard"}

    logo = await handle_image_generation({"type": "logo", ...})
    hero = await handle_image_generation({"type": "hero", ...})
    # Budget tracking via budget_tracking table
```

### Existing: Recraft Client API (fully implemented)
```python
# Source: tools/recraft_client.py
# Endpoint: POST https://external.api.recraft.ai/v1/images/generations
# Auth: Bearer token via RECRAFT_API_KEY env var
# Styles: realistic_image, digital_illustration, vector_illustration, icon
# Cost: $0.04/raster, $0.08/vector (V3/V4)
# Functions: generate_image, generate_logo, generate_hero_image,
#            generate_social_graphic, remove_background
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Text-only "21st.dev-style" references | Real component code fetching via REST API | This phase | Components used as structural inspiration in prompts |
| Recraft V3 at $0.04/image | Recraft V4 available at $0.04/image | Feb 2026 | Same price, better quality; update model param to "recraftv4" |
| 21st.dev free tier (5 requests) | $20/month plan recommended | Current | Free tier insufficient for production pipeline |

**Deprecated/outdated:**
- The `slug` field in `_CURATED_SOURCES` serves no purpose -- slugs are never resolved to actual URLs or code
- `RECRAFT_API_KEY` env var name in recraft_client.py vs `RECRAFT_API_TOKEN` mentioned in additional_context -- verify which is correct

## Open Questions

1. **21st.dev API key availability**
   - What we know: Need `TWENTYFIRST_API_KEY` env var; API base is `https://magic.21st.dev`
   - What's unclear: Whether the user has an API key or needs to sign up
   - Recommendation: Add `TWENTYFIRST_API_KEY=CHANGE_ME` to `.env.example`; make feature gracefully degrade when key is missing

2. **Recraft model version**
   - What we know: `recraft_client.py` defaults to `model="recraftv3"`; V4 is available at same price
   - What's unclear: Whether V4 produces better logos/hero images for this use case
   - Recommendation: Update default to `"recraftv3"` (stable); add `RECRAFT_MODEL` env var for easy upgrade to V4

3. **Full pipeline test coverage**
   - What we know: Only 20% of pipeline was exercised; Opus review + synthesis steps were skipped
   - What's unclear: Whether review/synthesis code paths work correctly
   - Recommendation: Include integration test that exercises full pipeline with mocked LLM/API responses

4. **RECRAFT_API_KEY vs RECRAFT_API_TOKEN**
   - What we know: `recraft_client.py` uses `RECRAFT_API_KEY`; additional context mentions `RECRAFT_API_TOKEN`
   - What's unclear: Which is actually set in `.env`
   - Recommendation: Verify `.env` has the correct key name; standardize on `RECRAFT_API_KEY` (matches code)

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| httpx | 21st.dev REST client | Yes | 0.28.1 | -- |
| Node.js/npx | NOT required (direct REST) | Yes | v25.8.1 | -- |
| mcp SDK | NOT used (direct REST preferred) | Yes | 1.26.0 | -- |
| RECRAFT_API_KEY | Recraft image generation | Yes (in .env) | -- | Skip asset generation |
| TWENTYFIRST_API_KEY | 21st.dev component fetching | Unknown | -- | Fall back to text-only references |
| Postgres | Budget tracking | Yes | -- | -- |

**Missing dependencies with no fallback:** None (all features degrade gracefully)

**Missing dependencies with fallback:**
- `TWENTYFIRST_API_KEY`: If missing, design_sources.py returns text-only references (current behavior) -- no degradation from current state

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | pyproject.toml / conftest.py |
| Quick run command | `PYTHONPATH=. python3 -m pytest tests/test_design_sources.py tests/test_build_site_gate.py -x -q` |
| Full suite command | `PYTHONPATH=. python3 -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TWENTY1-01 | 21st.dev client fetches components | unit | `PYTHONPATH=. pytest tests/test_twentyfirst_client.py -x` | Wave 0 |
| TWENTY1-02 | Component code injected into variant prompts | unit | `PYTHONPATH=. pytest tests/test_design_sources.py -x` | Exists (extend) |
| TWENTY1-03 | Components adapted to business context | unit | `PYTHONPATH=. pytest tests/test_design_sources.py -x` | Exists (extend) |
| RECRAFT-01 | Recraft generates logo + hero | unit | `PYTHONPATH=. pytest tests/test_recraft_pipeline.py -x` | Wave 0 |
| RECRAFT-02 | Budget tracking for Recraft calls | unit | `PYTHONPATH=. pytest tests/test_recraft_pipeline.py -x` | Wave 0 |
| RECRAFT-03 | Assets injected into variant HTML | unit | `PYTHONPATH=. pytest tests/test_build_site_gate.py -x` | Exists (extend) |
| PIPELINE-01 | Full pipeline runs end-to-end | integration | `PYTHONPATH=. pytest tests/test_e2e_pipeline.py -x` | Exists (extend) |
| PIPELINE-02 | Sites score higher with real components | integration | `PYTHONPATH=. pytest tests/test_site_quality_comparison.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `PYTHONPATH=. python3 -m pytest tests/test_twentyfirst_client.py tests/test_design_sources.py tests/test_build_site_gate.py -x -q`
- **Per wave merge:** `PYTHONPATH=. python3 -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_twentyfirst_client.py` -- covers TWENTY1-01 (client unit tests)
- [ ] `tests/test_recraft_pipeline.py` -- covers RECRAFT-01, RECRAFT-02 (asset generation + budget)
- [ ] `tests/test_site_quality_comparison.py` -- covers PIPELINE-02 (quality comparison)

## Sources

### Primary (HIGH confidence)
- [21st-dev/magic-mcp GitHub](https://github.com/21st-dev/magic-mcp) -- Source code: tool names, API endpoints, auth headers
- [21st-dev/magic-mcp fetch-ui.ts](https://raw.githubusercontent.com/21st-dev/magic-mcp/main/src/tools/fetch-ui.ts) -- FetchUiTool implementation details
- [21st-dev/magic-mcp http-client.ts](https://raw.githubusercontent.com/21st-dev/magic-mcp/main/src/utils/http-client.ts) -- API base URL (`https://magic.21st.dev`), auth header (`x-api-key`)
- `tools/recraft_client.py` -- Existing Recraft client, fully implemented
- `clawdbot/site_builder.py` -- Pipeline stages, integration points, existing prompts
- `clawdbot/design_sources.py` -- Current text-only 21st.dev references

### Secondary (MEDIUM confidence)
- [Recraft API Pricing](https://www.recraft.ai/docs/api-reference/pricing) -- $0.04/raster, $0.08/vector, unit-based billing
- [Glama MCP Registry](https://glama.ai/mcp/servers/@21st-dev/magic-mcp) -- Tool names and descriptions verified
- [Recraft API docs](https://docs.aimlapi.com/api-references/image-models/recraftai/recraft-v3) -- Styles, substyles, size parameters

### Tertiary (LOW confidence)
- 21st.dev pricing ($20/month paid plan) -- from review articles, not official pricing page
- Rate limits for 21st.dev API -- not documented anywhere; inferred from free tier (5 requests)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project, no new deps
- Architecture: HIGH -- integration points clearly identified in existing code
- Pitfalls: HIGH -- verified through code reading and API documentation
- 21st.dev API details: MEDIUM -- verified from source code, but rate limits unknown
- Recraft pricing: HIGH -- verified from official pricing page

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (stable APIs, unlikely to change)
