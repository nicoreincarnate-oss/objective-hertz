# TRIBE v2 Neuro-Scorer Integration Plan

> Run Meta's TRIBE v2 brain encoding model against every outbound email to predict neural activation patterns, score 4 cognitive dimensions, and build a self-improving learning loop that optimizes for the brain responses that actually convert.

**Created:** 2026-03-29
**Type:** New capability (integrates into existing Titan pipeline)
**License:** CC BY-NC-4.0 — internal tooling use only, not selling brain predictions. Removable if regulations change.
**Dependencies:** Phase 2 (Anti-Slop), Phase 4 (Middleware), Phase 7 (Adaptive Thresholds)
**Feature flag:** `ENABLE_NEURO_SCORER`

---

## Research References

| Source | Key Finding | How It Applies |
|--------|------------|----------------|
| [Meta TRIBE v2](https://github.com/facebookresearch/tribev2) | Predicts fMRI responses to text at 20,484 vertex resolution, zero-shot generalization across subjects | Runtime brain prediction engine — text in, cortical activation out |
| [PNAS Nexus 2025 — 16-study mega-analysis](https://academic.oup.com/pnasnexus/article/4/11/pgaf287/8313348) | vmPFC + reward circuits predict message effectiveness at population scale | Validates which ROIs to extract for conversion prediction |
| [PNAS 2024 — Neural responses to persuasion](https://www.pnas.org/doi/10.1073/pnas.2401317121) | DMN synchrony distinguishes persuaded vs resistant recipients | DMN activation = self-relevance signal for scoring |
| [PMC 2019 — Neural mechanisms + email campaigns](https://pmc.ncbi.nlm.nih.gov/articles/PMC6381231/) | Amygdala activity predicted email campaign engagement, moderated by vmPFC | Direct evidence: brain activation → email outcomes |
| [Frontiers 2012 — Message propagation](https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2012.00313/full) | VMPFC, MPFC, DMPFC, TPJ, MTL predict enthusiastic message sharing | Trust/mentalizing network drives word-of-mouth — relevant for referral signals |

---

## Architecture Overview

```
                    ┌─────────────────────────────────────────────┐
                    │           email_compose.py                  │
                    │  1. LLM drafts email (existing)             │
                    │  2. Anti-slop validation (existing Phase 2) │
                    │  3. ──→ NEURO SCORER (new) ──→              │
                    │     │   TRIBE v2 inference                  │
                    │     │   ROI extraction                      │
                    │     │   4-dimension scores                  │
                    │     │                                       │
                    │  4. Gate: scores above thresholds? ────────→│ Yes → email_queued
                    │     │ No → re-draft with neural guidance    │
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │           Outcome Collection                │
                    │  email_send → opened → replied → interested │
                    │  → demo_built → closed → paid               │
                    │  Each event maps back to stored neuro_scores│
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │           Learning Loop                     │
                    │  Daily reflection correlates neuro_scores   │
                    │  with outcomes per segment/industry/offer   │
                    │                                             │
                    │  Week 1-2: Baseline — score everything      │
                    │  Week 3+:  Correlations emerge              │
                    │  Week 5+:  Neural rules auto-extract        │
                    │  Week 8+:  Compose prompts weighted by      │
                    │            proven neural signatures         │
                    │  Ongoing:  LoRA training data includes      │
                    │            neural scores as metadata        │
                    └─────────────────────────────────────────────┘
```

---

## Sub-Phases

### Sub-Phase 8a: TRIBE v2 Local Deployment + Inference Service

**Goal:** Install TRIBE v2 on Mac Studio, build a Python service that takes email text and returns predicted cortical activation maps.

**Tasks:**
1. Install `tribev2` package + dependencies (LLaMA 3.2-3B access via HuggingFace)
2. Build `titan/neuro/tribe_service.py` — loads model once, exposes `predict_activation(text: str) -> np.ndarray` (shape: n_timesteps x 20,484 vertices)
3. Warm-start: model loads at Titan daemon startup, stays in memory
4. Benchmark inference latency for typical email lengths (50-150 words). Target: < 5s per email
5. Add health check endpoint to verify model is loaded and responsive
6. Add `ENABLE_NEURO_SCORER` feature flag to `system_config`

**Success criteria:**
- TRIBE v2 inference runs on Mac Studio without GPU OOM
- Single email prediction completes in < 5 seconds
- Model stays resident in memory between calls (no reload per email)
- Feature flag disables all neuro-scorer code paths cleanly

---

### Sub-Phase 8b: ROI Extraction + 4-Dimension Scoring

**Goal:** Map raw cortical vertex predictions to named brain regions, compute 4 neural dimension scores.

**Tasks:**
1. Build `titan/neuro/roi_extractor.py` — maps fsaverage5 vertices to Desikan-Killiany or Destrieux atlas regions
2. Define ROI-to-dimension mapping:

| Dimension | Atlas Regions (fsaverage5 labels) | Aggregation |
|-----------|----------------------------------|-------------|
| **Self-Relevance** (DMN core) | medialorbitofrontal, posteriorcingulate, precuneus, inferiorparietal | Mean activation across vertices in these labels |
| **Trust** (mentalizing network) | superiortemporal, bankssts, supramarginal, rostralmiddlefrontal | Mean activation |
| **Cognitive Load** (executive — inverse score) | caudalmiddlefrontal, lateralorbitofrontal, parsopercularis, parstriangularis | Inverse: 1 - normalized_mean |
| **Emotional Resonance** | amygdala*, insula, medialorbitofrontal (vmPFC overlap) | Mean activation |

*Note: amygdala is subcortical — may not be on fsaverage5 surface mesh. Fallback: use insula + vmPFC as proxy, or integrate volumetric prediction if TRIBE v2 supports it.*

3. Build `titan/neuro/neuro_scorer.py` — orchestrator that calls `tribe_service.predict_activation()` → `roi_extractor.extract_scores()` → returns `NeuroScores` dataclass:
   ```python
   @dataclass
   class NeuroScores:
       self_relevance: float    # 0.0-1.0
       trust: float             # 0.0-1.0
       cognitive_ease: float    # 0.0-1.0 (inverse of load)
       emotional_resonance: float  # 0.0-1.0
       composite: float         # weighted combination
       raw_roi_activations: dict  # full ROI breakdown for analysis
       timestamp: datetime
   ```
4. Normalization strategy: first 50 emails establish baseline distribution per dimension. Scores normalized to 0-1 using running percentile ranks (not absolute values — brain activation magnitudes are arbitrary).
5. Composite score: initially equal-weighted average. Weights update as learning loop discovers which dimensions predict conversion.

**Success criteria:**
- ROI extraction maps all target regions from fsaverage5 atlas
- 4 dimension scores produced for any input text
- Scores are normalized 0-1 with stable distributions after 50-email baseline
- Amygdala fallback strategy validated (surface proxy vs volumetric)

---

### Sub-Phase 8c: Pipeline Integration + Storage

**Goal:** Wire neuro-scorer into email_compose.py, store scores, implement gating.

**Tasks:**
1. Schema migration: add `neuro_scores JSONB` column to `email_sequences` table
   ```sql
   ALTER TABLE email_sequences ADD COLUMN neuro_scores JSONB;
   -- Stores: {self_relevance, trust, cognitive_ease, emotional_resonance, composite, raw_roi_activations}
   ```
2. Modify `email_compose.py`:
   - After LLM draft + anti-slop validation, call `neuro_scorer.score(email_text)`
   - Store scores in `email_sequences.neuro_scores`
   - **Gate logic (Phase 1 — permissive):** Log all scores but don't block any emails. Collect baseline data.
   - **Gate logic (Phase 2 — active, after 100+ scored emails):** If composite < threshold, re-draft with neural guidance prompt:
     ```
     "The previous draft scored low on [dimension]. Rewrite to increase [specific neural target]:
      - Self-relevance: reference their specific business context
      - Trust: use peer-to-peer tone, cite specific facts
      - Cognitive ease: shorter sentences, single CTA, remove jargon
      - Emotional resonance: connect to a real aspiration or pain point"
     ```
   - Max 2 re-draft attempts per email. If still below threshold after 2 attempts, send anyway with flag for review.
3. Modify `email_send.py` micro-simulation: add neuro_scores to simulation context (persona reviewers can reference neural quality)
4. Add neuro_scores to `training_data` metadata (enriches LoRA training examples)

**Success criteria:**
- Every composed email has neuro_scores in email_sequences
- Phase 1 (logging only) runs without blocking any emails
- Phase 2 (active gating) re-drafts emails below threshold
- Re-drafted emails score higher on target dimensions
- LoRA training data includes neural score metadata

---

### Sub-Phase 8d: Learning Loop — Outcome Correlation + Neural Rules

**Goal:** Daily reflection correlates neural scores with real outcomes. System discovers which brain activation patterns predict conversion for YOUR specific audience.

**Tasks:**
1. Modify `titan/memory.py` daily reflection:
   - Query `email_sequences` for recent emails with both `neuro_scores` and outcomes (opened, replied, interested, closed)
   - Compute Pearson correlation: each neural dimension vs each outcome signal
   - Segment by industry, lead_score tier, language, offer type
   - Store correlation findings as learnings in `titan_learnings` (category: `neural_optimization`)

2. New analysis queries:
   ```sql
   -- Which neural dimension best predicts replies?
   SELECT
     (neuro_scores->>'self_relevance')::float as sr,
     (neuro_scores->>'trust')::float as trust,
     (neuro_scores->>'cognitive_ease')::float as ease,
     (neuro_scores->>'emotional_resonance')::float as emo,
     CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END as replied
   FROM email_sequences
   WHERE neuro_scores IS NOT NULL
   AND sent_at > NOW() - INTERVAL '30 days';
   ```

3. Neural rule extraction (extends `extract_rules_from_reflection()`):
   - When a neural dimension shows statistically significant correlation with outcomes (p < 0.05, sample >= 30):
   - Auto-create rule in `titan_rules` (category: `neural_optimization`):
     ```
     "Self-relevance score > 0.72 correlates with 2.3x reply rate for e-commerce leads (n=47, p=0.003)"
     ```
   - Rules injected into compose prompts via existing `format_rules_for_prompt('neural_optimization')`

4. Composite weight updates:
   - Weekly: recalculate composite weights based on outcome correlations
   - Dimension with highest reply-rate correlation gets highest weight
   - Store weight history in `titan_learnings` for tracking evolution

5. Threshold adaptation:
   - If Phase 7 (Adaptive Thresholds / Bandit) is active: neural score thresholds managed by the bandit
   - If Phase 7 not yet active: simple rule — gate threshold = 25th percentile of scores from emails that got replies

**Success criteria:**
- Daily reflection reports neural score correlations
- Neural rules auto-created when statistical significance reached
- Composite weights shift toward conversion-predictive dimensions
- System improves over time: emails from week 8 score higher on predictive dimensions than week 1
- All learnings stored with confidence scores and sample sizes

---

### Sub-Phase 8e: Advanced Learning — LoRA Integration + Segment-Specific Models

**Goal:** Neural scores feed into LoRA fine-tuning and segment-specific optimization.

**Tasks:**
1. LoRA training data enrichment:
   - `export_training_data()` includes neural scores in metadata
   - Training format adds neural target to instruction:
     ```json
     {
       "instruction": "Write a cold outreach email. Target neural profile: self_relevance>0.75, trust>0.70, cognitive_ease>0.80, emotional_resonance>0.65",
       "input": "{research, industry, language}",
       "output": "{subject, body}"
     }
     ```
   - Only emails with reply outcomes AND neuro_scores qualify as training examples

2. Segment-specific neural profiles:
   - After 100+ scored emails per segment, compute segment's "ideal neural signature"
   - Store in `titan_learnings` as segment profiles:
     ```
     "e-commerce-mx: {sr: 0.78, trust: 0.65, ease: 0.82, emo: 0.71}"
     "saas-us: {sr: 0.68, trust: 0.81, ease: 0.75, emo: 0.59}"
     ```
   - Compose prompt includes target neural profile for the lead's segment

3. A/B tracking (if Phase 7 bandit active):
   - Neural-guided emails vs standard emails as bandit arms
   - Track: open rate, reply rate, meeting rate, close rate per arm
   - Bandit auto-allocates more volume to winning arm

4. Weekly neural strategy report (extension of existing weekly review):
   - Best/worst performing neural profiles
   - Dimension drift tracking (are scores improving over time?)
   - Segment-level insights
   - Stored in `titan_learnings` (category: `weekly_strategy`)

**Success criteria:**
- LoRA training includes neural targets in instruction
- Segment-specific neural profiles computed and used in compose
- A/B tracking shows neural-guided outperforms standard
- Weekly report surfaces actionable neural insights

---

## Data Model Changes

### New Columns

| Table | Column | Type | Purpose |
|-------|--------|------|---------|
| `email_sequences` | `neuro_scores` | JSONB | Per-email neural dimension scores + raw ROI data |

### New Rows in Existing Tables

| Table | Category | Content |
|-------|----------|---------|
| `titan_learnings` | `neural_optimization` | Dimension-outcome correlations per segment |
| `titan_learnings` | `neural_profile` | Segment-specific ideal neural signatures |
| `titan_rules` | `neural_optimization` | Data-proven neural rules (e.g., "trust > 0.71 → 2.1x replies") |
| `system_config` | `ENABLE_NEURO_SCORER` | Feature flag |
| `training_data` | metadata includes neuro_scores | Enriched LoRA training examples |

---

## New Files

| File | Purpose |
|------|---------|
| `titan/neuro/__init__.py` | Neuro-scorer package |
| `titan/neuro/tribe_service.py` | TRIBE v2 model loading, text-to-activation inference |
| `titan/neuro/roi_extractor.py` | fsaverage5 vertex → atlas region mapping, ROI aggregation |
| `titan/neuro/neuro_scorer.py` | Orchestrator: draft in → NeuroScores out |
| `titan/neuro/normalizer.py` | Running percentile normalization (baseline calibration) |
| `tests/test_neuro_scorer.py` | Unit tests for scoring, ROI extraction, normalization |

---

## Feature Flag

| Flag | Default | What It Controls |
|------|---------|-----------------|
| `ENABLE_NEURO_SCORER` | false | All TRIBE v2 inference, neural scoring, gating, and learning loop |

When disabled: zero overhead. No model loaded, no inference, no scores computed. Pipeline runs exactly as before.

---

## Dependencies on Other Phases

| Phase | Dependency Type | Why |
|-------|----------------|-----|
| Phase 2 (Anti-Slop) | Soft | Neuro-scorer runs after anti-slop. Could run independently but best in sequence. |
| Phase 4 (Middleware) | Soft | Neuro-scorer could be a middleware layer. Not required — can be direct integration. |
| Phase 7 (Adaptive Thresholds) | Soft | Bandit manages neural thresholds. Without it, uses simple percentile-based thresholds. |

**Can run independently of all phases** — only requires the existing email_compose.py and email_sequences table.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| TRIBE v2 inference too slow (>5s/email) | Medium | Medium | Batch scoring: score N drafts in parallel. Or pre-compute for template segments. |
| Amygdala not on fsaverage5 surface | High | Low | Use insula + vmPFC as emotional resonance proxy. Validate against literature. |
| Neural scores don't correlate with outcomes | Medium | High | First 100 emails are logging-only. If no signal after 200 emails, revisit ROI selection or scoring method. Feature flag lets you kill it cleanly. |
| CC BY-NC license challenged | Low | Medium | Internal use only, not selling predictions. Removable — all code behind feature flag. No vendor lock-in. |
| Model memory footprint too large | Low | Low | Mac Studio has 192GB unified memory. LLaMA 3.2-3B + TRIBE transformer < 10GB. |

---

## Timeline Estimate

| Sub-Phase | Scope |
|-----------|-------|
| 8a: TRIBE v2 deployment | Install, service wrapper, benchmarking |
| 8b: ROI extraction + scoring | Atlas mapping, 4 dimensions, normalization |
| 8c: Pipeline integration | Schema, compose integration, gating, storage |
| 8d: Learning loop | Correlation analysis, rule extraction, weight updates |
| 8e: Advanced learning | LoRA enrichment, segment profiles, A/B tracking |

Each sub-phase is independently deployable behind the feature flag. 8a-8c gets you scoring. 8d-8e gets you the learning loop.

---

*Plan created: 2026-03-29 — TRIBE v2 Neuro-Scorer for Titan pipeline*
