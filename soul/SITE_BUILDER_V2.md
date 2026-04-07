# ClawdBot Site Builder v2 — Architecture

Technical reference for the section-based visual site builder introduced
in Phases 33-39. This replaces the monolithic `site_builder.build_site`
v1 path with a pipeline of section agents coordinated by a build plan,
assembled into a single page, and verified by VLM full-page QA.

## System Diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│ lead dict (industry, city, brand, copy hints, tier)                    │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │
                 create_build_plan │ clawdbot.section_planner
                                  ▼
           ┌──────────────────────────────────────────┐
           │ BuildPlan                                │
           │   design_tokens (colors, fonts, spacing) │
           │   direction  (cosmos-dark-curation …)    │
           │   sections   [hero, features, …]         │
           │   tier       BuildTier (demo | premium)  │
           └──────────────┬───────────────────────────┘
                          │
      validate_design_contract │ clawdbot.design_tokens
                          │    (contrast / readability / aesthetic)
                          ▼
           ┌──────────────────────────────────────────┐
           │ build_all_sections  (parallel, semaphore │
           │   _LLM_CONCURRENCY=5, _VLM_CONCURRENCY=2)│
           │                                          │
           │   for each section:                      │
           │     select snippet (snippet_registry)    │
           │     generate_section (LLM)               │
           │     render_html_to_png (Playwright)      │
           │     score_section_quality (VLM)          │
           │     iterate up to SECTION_MAX_ITERATIONS │
           └──────────────┬───────────────────────────┘
                          │
             assemble_page │ clawdbot.page_assembler
                          ▼
           ┌──────────────────────────────────────────┐
           │ Full HTML doc with shared <head>, nav,   │
           │ all sections, footer, taste tokens       │
           └──────────────┬───────────────────────────┘
                          │
             score_full_page │ clawdbot.visual_scorer (10-pt rubric)
                          ▼
                 ┌────────┴────────┐
                 │ mandatory_pass? │
                 └─┬─────────────┬─┘
             yes   │             │  no + VISUAL_QA_BLOCKING=true
                   ▼             ▼
             deploy          block deploy,
             (Netlify/       surface reason
             Coolify)
```

## Configuration Reference

All flags are env vars (read at module import unless noted as dynamic).

| Variable | Default | Purpose |
|---|---|---|
| `CLAWDBOT_V2_ENABLED` | `true` | Master switch. False falls back to v1 path. |
| `VISUAL_QA_ENABLED` | `true` | Run VLM full-page QA at all. |
| `VISUAL_QA_BLOCKING` | `false` | If true, failing QA prevents deploy. |
| `VISUAL_QA_MIN_THRESHOLD` | `7.0` | Minimum 10-pt rubric total to pass. |
| `VISUAL_QA_VLM_PROVIDER` | `ollama` | `ollama` (Qwen2.5-VL) or `claude` (Sonnet Vision). |
| `DESIGN_TOKENS_ENABLED` | `true` | Inject design token CSS vars into each snippet. |
| `SECTION_MAX_ITERATIONS` | `2` | Max retries per section when VLM score < threshold. |
| `SECTION_MIN_SCORE` | `6.5` | Per-section overall score threshold. |
| `TASTE_OVERLAY_ENABLED` | `false` | Weight direction choice by operator taste profile. |
| `REFERENCE_COMPARISON_ENABLED` | `true` | Compare sections to curated references in addition to quality-only scoring. |
| `PLAYWRIGHT_POOL_SIZE` | `3` | Number of pre-warmed Chromium contexts. |
| `LLM_MODEL_FAST` | `claude-haiku-*` | Model used for iteration patches. |
| `LLM_MODEL_SMART` | `claude-sonnet-*` | Default section generation model. |
| `OLLAMA_URL` | `http://localhost:11434` | Endpoint for Qwen2.5-VL. |

## Cost Analysis

### Demo tier — $0.00
- All generation via `ollama:qwen2.5:14b` (local)
- All scoring via `ollama:qwen2.5-vl` (local)
- Playwright renders are free
- Netlify/Coolify deploy free tier

### Premium tier — ~$0.19 per build
| Stage | Model | Calls | Tokens | Cost |
|---|---|---|---|---|
| Plan generation | Sonnet | 1 | ~3k out | $0.045 |
| Section generation | Sonnet | 6 | ~1.5k out each | $0.135 |
| Section iteration patches | Haiku | 0-6 | ~0.5k out | $0.003 |
| Full-page QA | Claude Vision | 1 | 1k in + 0.5k out | $0.008 |
| **Total** | | | | **~$0.19** |

Cost targets enforced by `shared/llm_client.py` budget guard.

## Troubleshooting

### Playwright not installed
Symptom: `RuntimeError: playwright is required`
Fix: `pip install playwright && playwright install chromium`

### Ollama not running / model not pulled
Symptom: `httpx.ConnectError` or `model 'qwen2.5-vl' not found`
Fix: Start Ollama (`ollama serve`) and `ollama pull qwen2.5-vl`

### VLM parse errors ("raw" in score dict)
Symptom: All section scores default to 0, `issues: ['vlm_parse_failed']`
Fix: VLM returning malformed JSON. Run `python -m clawdbot.vlm_calibration`
to diagnose. If separation gap < 3.0, tighten the prompt in
`visual_scorer._score_prompt` or switch `VISUAL_QA_VLM_PROVIDER=claude`.

### Rate limits on Claude API
Symptom: `RateLimitError` during premium build
Fix: Lower `_LLM_CONCURRENCY` in `section_orchestrator.py` from 5 to 2-3.
Budget guard should already be throttling; check `budget_tracking` table.

### Snippets degrading over time
Symptom: Builds look worse than they used to
Fix: Run `python -m clawdbot.snippet_validator` to find broken or
low-scoring snippets. Fix or retire flagged snippets.

### Reference library outdated
Symptom: Section agents producing output that diverges from reference sites
Fix: Refresh the library — see *Reference Library Maintenance* below.

## Reference Library Maintenance

The reference library (`soul/references/`) seeds section agents with
concrete visual targets per design direction. Maintenance cadence:
**quarterly refresh**, or whenever a reference site redesigns.

Steps:
1. Edit `soul/references/curated_sites.yaml` if sites need adding/removing.
2. Run `python -m scripts.capture_references` (manual op — hits live web).
3. Verify crops in `soul/references/{section_type}/{direction}.png` look right.
4. Run `python -m clawdbot.vlm_calibration` to confirm calibration still passes.
5. Commit the refreshed PNGs and `metadata.yaml` files.

Never delete a reference without replacing it — section agents use these
as anchors and a missing reference falls back to quality-only scoring.

## VLM Calibration Procedure

Run at minimum once per VLM provider change (switching Ollama models,
switching to Claude Vision, etc.).

1. Ensure `soul/qa-calibration/good/` and `soul/qa-calibration/bad/`
   contain the 20+20 PNGs referenced in the manifests.
2. Run:
   ```bash
   python -c "import asyncio; from clawdbot.vlm_calibration import calibrate_vlm_scorer; \
     r = asyncio.run(calibrate_vlm_scorer()); print(r)"
   ```
3. Inspect `soul/qa-calibration/last_run.yaml`.
4. Check:
   - `good_mean_score >= 7.5`
   - `bad_mean_score <= 4.0`
   - `separation_gap >= 3.0`
   - `passed == true`
5. If failed, examine `per_dimension_accuracy` — any dimension near 0.5
   is noise. Fix the VLM prompt for that dimension in
   `visual_scorer._score_prompt` and re-run.

## Module Map

Phase 33-39 additions, with one-line purposes.

| File | Phase | Purpose |
|---|---|---|
| `clawdbot/snippet_registry.py` | 33 | Load + select curated HTML snippets per section/direction. |
| `clawdbot/design_tokens.py` | 33 | DesignTokens dataclass, CSS var / Tailwind config emission. |
| `clawdbot/taste_profile.py` | 33 | Operator taste profile + direction weighting. |
| `clawdbot/renderer.py` | 34 | PlaywrightPool + render_html_to_png. |
| `clawdbot/visual_scorer.py` | 34 | SectionScore, ComparisonScore, FullPageScore; VLM scoring. |
| `clawdbot/reference_manager.py` | 34 | Reference screenshot storage + decomposition. |
| `clawdbot/section_planner.py` | 35 | BuildPlan creation, BuildTier (demo/premium). |
| `clawdbot/section_agent.py` | 36 | Per-section generator + iteration loop. |
| `clawdbot/section_orchestrator.py` | 36 | Parallel section dispatch, cost tracking. |
| `clawdbot/page_assembler.py` | 37 | Combine sections into full HTML document. |
| `clawdbot/site_builder.py::build_site_v2` | 38 | Top-level v2 entry point with v1 fallback. |
| `soul/references/curated_sites.yaml` | 39 | 30 reference sites across 6 directions. |
| `scripts/capture_references.py` | 39 | Capture + decompose reference sites. |
| `clawdbot/vlm_calibration.py` | 39 | Good/bad calibration harness + separation gap metric. |
| `clawdbot/snippet_validator.py` | 39 | Monthly snippet render+score+static checks. |
| `clawdbot/performance_profiler.py` | 39 | Stage-by-stage build profiling. |
| `soul/qa-calibration/{good,bad}/manifest.yaml` | 39 | Calibration dataset manifests. |
| `soul/SITE_BUILDER_V2.md` | 39 | This document. |
