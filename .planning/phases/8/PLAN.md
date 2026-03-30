# Phase 8: TRIBE v2 Neuro-Scorer

**Goal:** Predict neural activation across 4 cognitive dimensions for every outbound email/site/message using Meta's TRIBE v2 brain encoding model. Gate low-scoring drafts for re-generation. Build a closed-loop learning system that correlates predicted brain responses with real conversion outcomes.
**Requirements:** NEURO-01 through NEURO-10
**Depends on:** Phase 2 (anti-slop infrastructure), Phase 4 (middleware chain)
**Soft depends:** Phase 7 (adaptive thresholds manage neural gates)
**Feature flag:** `ENABLE_NEURO_SCORER`
**License:** CC-BY-NC-4.0 (internal tooling — not selling predictions)

---

## Context

Meta's TRIBE v2 (TRImodal Brain Encoder) predicts ~70K fMRI brain voxels from text input using LLaMA 3.2-3B as a frozen text encoder and an 8-layer transformer. Released March 26, 2026. Open-sourced at github.com/facebookresearch/tribev2 and HuggingFace facebook/tribev2.

The 4 cognitive dimensions map to established brain regions:

| Dimension | Brain Regions | Neuroscience Source |
|-----------|--------------|-------------------|
| Self-relevance | mPFC, PCC, precuneus, inferiorparietal (default mode network) | Northoff & Bermpohl 2004; Andrews-Hanna 2012 |
| Trust | superiortemporal, bankssts, supramarginal, rostralmiddlefrontal (mentalizing network) | Adolphs 2002; Delgado et al. 2005 |
| Cognitive ease | parsopercularis, parstriangularis, caudalmiddlefrontal (INVERSE — low activation = easy) | Oppenheimer 2008 |
| Emotional resonance | insula, medialorbitofrontal/vmPFC, amygdala proxy | Pessoa 2008; Barrett & Satpute 2013 |

### Current State

| Component | File | Status |
|-----------|------|--------|
| TRIBE v2 | Not installed | github.com/facebookresearch/tribev2 |
| Email compose | `titan/pipeline/email_compose.py` | LLM generates + anti-slop validates (Phase 2) |
| Middleware chain | `shared/middleware.py` | Built in Phase 4 |
| quality_scores table | Phase 2 | Exists |
| neuro_scores column | email_sequences | Does not exist |
| Hardware | Mac M4 32GB | MPS (Metal Performance Shaders) available |

---

## Tasks

### Task 1: MPS Validation Spike (NEURO-01)
**File:** `scripts/tribe-validation-spike.py` (new, throwaway)
**What:**

2-day timeboxed validation on M4 32GB:
1. Install `tribev2` package: `pip install -e .` from cloned repo
2. Download model weights: `TribeModel.from_pretrained("facebook/tribev2")`
3. Load LLaMA 3.2-3B (frozen encoder) — measure memory footprint
4. Run text inference on sample email (100 words): `model.predict(events=df)`
5. Measure: memory usage, inference latency, MPS vs CPU comparison
6. Test lazy load/unload cycle: load model → predict → unload → verify memory freed

**Benchmarks to capture:**

| Metric | Target | Failure threshold |
|--------|--------|-------------------|
| Model memory (loaded) | < 10GB | > 16GB → won't fit alongside daemons |
| Inference latency (MPS) | < 8s per email | > 15s → too slow for pipeline |
| Memory after unload | < 1GB residual | > 4GB → memory leak |
| Load time (cold start) | < 30s | > 60s → unacceptable wait |

**Decision gate:**
- **PASS**: Latency < 8s on MPS, memory < 10GB → proceed with direct TRIBE v2
- **PARTIAL**: Latency 8-15s → batch scoring (score N emails together, not inline)
- **FAIL**: Won't load on 32GB → fall back to Haiku-based proxy scorer with TRIBE-informed rubric

**Acceptance criteria:**
- [ ] Spike script runs on M4 32GB with MPS
- [ ] Benchmark results documented in `.planning/phases/8/SPIKE-RESULT.md`
- [ ] Go/no-go decision recorded

### Task 2: Model Service with Lazy Loading (NEURO-02)
**File:** `titan/neuro/tribe_service.py` (new)
**What:**

```python
class TribeService:
    """TRIBE v2 inference service with lazy load/unload for M4 32GB memory management."""

    _instance: "TribeService | None" = None
    _model = None
    _loaded = False

    @classmethod
    def get(cls) -> "TribeService":
        """Singleton access."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def predict_activation(self, text: str) -> np.ndarray:
        """Text → predicted cortical activation map.

        Returns: ndarray shape (n_timesteps, n_vertices) on fsaverage5 surface.
        Lazy loads model on first call, keeps in memory for subsequent calls.
        """
        if not self._loaded:
            await self._load_model()

        events_df = self._text_to_events(text)
        with torch.no_grad():
            activation = self._model.predict(events=events_df)
        return activation

    async def _load_model(self) -> None:
        """Load TRIBE v2 model weights. ~8-10GB on M4."""
        import torch
        from tribev2 import TribeModel

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._model = TribeModel.from_pretrained(
            "facebook/tribev2", cache_folder="./cache"
        ).to(device)
        self._loaded = True
        logger.info(f"TRIBE v2 loaded on {device}")

    async def unload(self) -> None:
        """Free model memory when not needed."""
        if self._model is not None:
            del self._model
            self._model = None
            self._loaded = False
            import gc; gc.collect()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            logger.info("TRIBE v2 unloaded, memory freed")

    def _text_to_events(self, text: str) -> "pd.DataFrame":
        """Convert email text to events DataFrame for TRIBE v2 input."""
        return self._model.get_events_dataframe(text=text)
```

Key design decisions:
- **Singleton**: One model instance shared across all scoring calls
- **Lazy loading**: Model not loaded until first scoring request
- **Explicit unload**: `unload()` for memory recovery when not scoring
- **MPS preferred**: Falls back to CPU if MPS unavailable

**Acceptance criteria:**
- [ ] Model loads on demand, not at daemon startup
- [ ] `unload()` frees memory to < 1GB residual
- [ ] MPS acceleration used when available
- [ ] Multiple sequential predictions work without reloading

### Task 3: ROI Extraction + 4-Dimension Scoring (NEURO-03, NEURO-04)
**File:** `titan/neuro/roi_extractor.py` (new)
**What:**

```python
# Desikan-Killiany atlas region mappings for fsaverage5 surface
DIMENSION_ROIS = {
    "self_relevance": [
        "medialorbitofrontal",   # mPFC
        "posteriorcingulate",    # PCC
        "precuneus",             # precuneus
        "inferiorparietal",      # angular gyrus
    ],
    "trust": [
        "superiortemporal",      # STS
        "bankssts",              # posterior STS
        "supramarginal",         # SMG
        "rostralmiddlefrontal",  # rMFG
    ],
    "cognitive_load": [  # NOTE: inverted to get "ease"
        "caudalmiddlefrontal",   # premotor
        "lateralorbitofrontal",  # lOFC
        "parsopercularis",       # Broca's (BA44)
        "parstriangularis",      # Broca's (BA45)
    ],
    "emotional_resonance": [
        "insula",                # anterior insula
        "medialorbitofrontal",   # vmPFC overlap
        "rostralanteriorcingulate",  # rACC
    ],
}

class ROIExtractor:
    """Maps TRIBE v2 vertex predictions to named brain regions."""

    def __init__(self):
        self._atlas = self._load_atlas()

    def _load_atlas(self) -> dict[str, np.ndarray]:
        """Load fsaverage5 Desikan-Killiany parcellation."""
        # nibabel or provided by tribev2 package
        ...

    def extract_scores(self, activation: np.ndarray) -> dict[str, float]:
        """Extract 4 cognitive dimension scores from vertex activation map."""
        scores = {}
        for dimension, regions in DIMENSION_ROIS.items():
            region_activations = []
            for region in regions:
                vertex_indices = self._atlas.get(region, [])
                if len(vertex_indices) > 0:
                    region_mean = np.mean(activation[:, vertex_indices])
                    region_activations.append(region_mean)

            if region_activations:
                scores[dimension] = float(np.mean(region_activations))

        # Invert cognitive_load → cognitive_ease
        if "cognitive_load" in scores:
            scores["cognitive_ease"] = -scores.pop("cognitive_load")

        return scores
```

**File:** `titan/neuro/normalizer.py` (new)

```python
class RunningNormalizer:
    """Running percentile normalization for neuro-scores.

    First 50 emails establish baseline distribution per dimension.
    After baseline: scores normalized to 0-1 using percentile ranks.
    Warm-start: initial distribution seeded from published neuroscience baselines.
    """

    BASELINE_SIZE = 50

    def __init__(self):
        self._history: dict[str, list[float]] = defaultdict(list)
        self._baseline_ready = False

    def normalize(self, raw_scores: dict[str, float]) -> dict[str, float]:
        """Normalize raw scores to 0-1 using running percentile."""
        normalized = {}
        for dim, value in raw_scores.items():
            self._history[dim].append(value)
            if len(self._history[dim]) >= self.BASELINE_SIZE:
                percentile = percentileofscore(self._history[dim], value) / 100.0
                normalized[dim] = percentile
            else:
                # Pre-baseline: use warm-start (center at 0.5)
                normalized[dim] = 0.5 + (value / (abs(value) + 1e-6)) * 0.3
        return normalized
```

**File:** `titan/neuro/neuro_scorer.py` (new) — orchestrator

```python
@dataclass
class NeuroScores:
    self_relevance: float      # 0.0-1.0
    trust: float               # 0.0-1.0
    cognitive_ease: float      # 0.0-1.0
    emotional_resonance: float # 0.0-1.0
    composite: float           # weighted combination
    raw_roi_activations: dict  # full ROI breakdown
    dimension_weights: dict    # current composite weights

class NeuroScorer:
    """Orchestrates TRIBE v2 prediction → ROI extraction → normalization → scoring."""

    DEFAULT_WEIGHTS = {
        "self_relevance": 0.30,
        "trust": 0.25,
        "cognitive_ease": 0.20,
        "emotional_resonance": 0.25,
    }

    async def score(self, text: str) -> NeuroScores:
        """Score text on 4 cognitive dimensions."""
        service = TribeService.get()
        activation = await service.predict_activation(text)

        extractor = ROIExtractor()
        raw_scores = extractor.extract_scores(activation)

        normalizer = RunningNormalizer()
        normalized = normalizer.normalize(raw_scores)

        weights = await self._get_current_weights()
        composite = sum(normalized[d] * weights[d] for d in normalized)

        return NeuroScores(
            self_relevance=normalized.get("self_relevance", 0.5),
            trust=normalized.get("trust", 0.5),
            cognitive_ease=normalized.get("cognitive_ease", 0.5),
            emotional_resonance=normalized.get("emotional_resonance", 0.5),
            composite=composite,
            raw_roi_activations=raw_scores,
            dimension_weights=weights,
        )

    async def _get_current_weights(self) -> dict[str, float]:
        """Load learned weights from system_config, or use defaults."""
        ...
```

**Acceptance criteria:**
- [ ] ROI extraction maps all target Desikan-Killiany regions from fsaverage5
- [ ] 4 dimension scores produced for any input text
- [ ] Cognitive load inverted to cognitive ease
- [ ] Running percentile normalization produces 0-1 scores after 50-email baseline
- [ ] Warm-start prevents garbage scores during cold-start period

### Task 4: Email Pipeline Integration + Storage (NEURO-05, NEURO-06, NEURO-07)
**Files:**
- `titan/pipeline/email_compose.py` (modify)
- `scripts/migrations/024-neuro-scores.sql` (new)

**Migration:**
```sql
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS neuro_scores JSONB;
CREATE INDEX idx_email_sequences_neuro ON email_sequences ((neuro_scores->>'composite'))
    WHERE neuro_scores IS NOT NULL;
```

**Pipeline integration in email_compose.py:**
```python
# After anti-slop validation (Phase 2), before queueing:
if feature_flag("ENABLE_NEURO_SCORER"):
    scorer = NeuroScorer()
    neuro = await scorer.score(email_body)

    # Store scores
    await execute(
        "UPDATE email_sequences SET neuro_scores = %s WHERE seq_id = %s",
        (Jsonb(asdict(neuro)), seq_id),
    )

    # Phase 1 (first 100 emails): log only, don't gate
    scored_count = await fetch_val("SELECT COUNT(*) FROM email_sequences WHERE neuro_scores IS NOT NULL")
    if scored_count > 100 and neuro.composite < await _get_neuro_threshold():
        # Re-draft with neural guidance
        weak_dims = [d for d in ["self_relevance", "trust", "cognitive_ease", "emotional_resonance"]
                     if getattr(neuro, d) < 0.4]
        guidance = _build_neural_guidance(weak_dims)
        email_body = await _redraft_with_guidance(email_body, guidance, lead, research)

        # Re-score
        neuro2 = await scorer.score(email_body)
        if _composite_score(neuro2) > _composite_score(neuro):
            neuro = neuro2
            # Update stored scores
```

**Neural guidance prompts:**
```python
NEURAL_GUIDANCE = {
    "self_relevance": "Reference their specific business name, location, and situation. "
                      "Use 'you' and 'your'. Connect to their daily reality.",
    "trust": "Use peer-to-peer tone. Cite a specific fact from research. "
             "Mention a real detail only someone who looked at their business would know.",
    "cognitive_ease": "Shorter sentences. Single clear CTA. Remove jargon. "
                      "One idea per paragraph. Under 80 words total.",
    "emotional_resonance": "Connect to a real aspiration (growth, freedom, reputation) "
                           "or pain point (losing customers, wasting time). Be specific, not generic.",
}
```

**Extend to all content types (NEURO-06):**
- `clawdbot/site_builder.py`: Score site copy sections before deploy
- `titan/pipeline/follow_up.py`: Score follow-up emails
- `hermes/alerts.py`: Score operator alerts (relaxed thresholds)

**Acceptance criteria:**
- [ ] Every composed email has neuro_scores in email_sequences
- [ ] First 100 emails: logging only (no gating)
- [ ] After 100 emails: low-scoring drafts re-drafted with dimension-specific guidance
- [ ] Re-drafted emails score higher on target dimensions (measured)
- [ ] Site copy, follow-ups, and alerts also scored when flag enabled
- [ ] Max 2 re-draft attempts per email

### Task 5: Closed-Loop Learning (NEURO-08, NEURO-09)
**File:** `titan/neuro/learning_loop.py` (new)
**What:**

```python
async def neural_reflection() -> dict:
    """Daily reflection: correlate neuro-scores with conversion outcomes.

    Run via Perseus scheduler daily after 50+ scored emails exist.
    """
    # Query emails with both neuro_scores and outcomes
    data = await fetch_all("""
        SELECT
            es.neuro_scores,
            CASE WHEN c.status IN ('replied', 'interested', 'demo_built',
                                    'proposal_sent', 'negotiating', 'closed', 'paid')
                 THEN 1 ELSE 0 END as converted,
            c.industry, c.lead_score
        FROM email_sequences es
        JOIN clients c ON es.client_id = c.id
        WHERE es.neuro_scores IS NOT NULL
        AND es.sent_at > NOW() - INTERVAL '30 days'
    """)

    if len(data) < 50:
        return {"status": "insufficient_data", "count": len(data)}

    # Compute correlations per dimension
    correlations = {}
    for dim in ["self_relevance", "trust", "cognitive_ease", "emotional_resonance"]:
        scores = [float(r["neuro_scores"][dim]) for r in data]
        outcomes = [r["converted"] for r in data]
        corr, p_value = pearsonr(scores, outcomes)
        correlations[dim] = {"r": corr, "p": p_value, "n": len(data)}

    # Auto-extract rules when significant (NEURO-09)
    for dim, stats in correlations.items():
        if stats["p"] < 0.05 and stats["n"] >= 30:
            await _create_neural_rule(dim, stats)

    # Update composite weights based on correlations
    await _update_composite_weights(correlations)

    return {"status": "complete", "correlations": correlations}

async def _create_neural_rule(dim: str, stats: dict) -> None:
    """Auto-create titan_rule from statistically significant neural finding."""
    rule_text = (
        f"Neuro-scorer: {dim} (r={stats['r']:.2f}, p={stats['p']:.3f}, n={stats['n']}) "
        f"correlates with conversion. Prioritize {dim} in compose prompts."
    )
    await execute(
        """INSERT INTO titan_rules (category, rule_text, source, confidence)
           VALUES ('neural_optimization', %s, 'neuro_learning_loop', %s)
           ON CONFLICT DO NOTHING""",
        (rule_text, abs(stats["r"])),
    )

async def _update_composite_weights(correlations: dict) -> None:
    """Shift composite weights toward conversion-predictive dimensions."""
    total_abs_r = sum(abs(c["r"]) for c in correlations.values() if c["p"] < 0.1)
    if total_abs_r < 0.01:
        return  # No signal yet

    new_weights = {}
    for dim, stats in correlations.items():
        if stats["p"] < 0.1:
            new_weights[dim] = abs(stats["r"]) / total_abs_r
        else:
            new_weights[dim] = 0.25  # fallback to equal weight

    await set_config("neuro_composite_weights", new_weights)
```

Add daily job to `perseus/scheduler.py`:
```python
{"name": "neural_reflection", "fn": neural_reflection, "cron": "0 3 * * *"}
```

**Acceptance criteria:**
- [ ] Daily reflection computes dimension-outcome correlations
- [ ] Neural rules auto-created when p < 0.05 and n >= 30
- [ ] Composite weights shift toward conversion-predictive dimensions
- [ ] All findings stored with confidence scores and sample sizes

### Task 6: Segment-Specific Neural Profiles (NEURO-10)
**File:** `titan/neuro/learning_loop.py` (extend)
**What:**

```python
async def compute_segment_profiles() -> dict:
    """Compute ideal neural signature per industry segment.

    Run weekly. Requires 100+ scored emails per segment.
    """
    segments = await fetch_all("""
        SELECT c.industry, AVG((es.neuro_scores->>'self_relevance')::float) as avg_sr,
               AVG((es.neuro_scores->>'trust')::float) as avg_trust,
               AVG((es.neuro_scores->>'cognitive_ease')::float) as avg_ease,
               AVG((es.neuro_scores->>'emotional_resonance')::float) as avg_emo,
               COUNT(*) as n
        FROM email_sequences es
        JOIN clients c ON es.client_id = c.id
        WHERE es.neuro_scores IS NOT NULL
        AND c.status IN ('replied', 'interested', 'closed', 'paid')
        GROUP BY c.industry
        HAVING COUNT(*) >= 100
    """)

    profiles = {}
    for seg in segments:
        profiles[seg["industry"]] = {
            "self_relevance": seg["avg_sr"],
            "trust": seg["avg_trust"],
            "cognitive_ease": seg["avg_ease"],
            "emotional_resonance": seg["avg_emo"],
        }
        # Store as titan_learning
        await store_memory(
            f"neural_profile_{seg['industry']}",
            json.dumps(profiles[seg["industry"]]),
            namespace="titan",
            category="neural_profile",
        )

    return profiles
```

Inject into compose prompts:
```python
# In email_compose.py, when ENABLE_NEURO_SCORER:
segment_profile = await _get_neural_profile(lead["industry"])
if segment_profile:
    prompt += f"\n\nTarget neural profile for {lead['industry']}: "
    prompt += f"self_relevance>{segment_profile['self_relevance']:.2f}, "
    prompt += f"trust>{segment_profile['trust']:.2f}, "
    prompt += f"cognitive_ease>{segment_profile['cognitive_ease']:.2f}, "
    prompt += f"emotional_resonance>{segment_profile['emotional_resonance']:.2f}"
```

**Acceptance criteria:**
- [ ] Segment profiles computed weekly for segments with 100+ emails
- [ ] Profiles stored in titan_learnings for MAGMA integration
- [ ] Compose prompts include target neural profile for lead's segment
- [ ] Profiles update as more data accumulates

### Task 7: Middleware Integration
**File:** `shared/middleware.py` (extend, if Phase 4 complete)
**What:**

Add `neuro_scorer_middleware` to the chain:

```python
async def neuro_scorer_middleware(ctx: dict, next_fn: NextFn) -> StageResult:
    """Score content-producing stages with TRIBE v2 neuro-scorer."""
    if not feature_flag("ENABLE_NEURO_SCORER"):
        return await next_fn(ctx)

    CONTENT_STAGES = {"email_compose", "site_build", "follow_up"}
    if ctx.get("stage_name") not in CONTENT_STAGES:
        return await next_fn(ctx)

    result = await next_fn(ctx)

    if result.get("success") and result.get("output"):
        scorer = NeuroScorer()
        neuro = await scorer.score(result["output"])
        result["neuro_scores"] = asdict(neuro)

    return result
```

Update middleware ordering:
```python
TITAN_MIDDLEWARE = [
    "budget_check",
    "dna_guard",
    "anti_slop",
    "neuro_scorer",  # NEW: after anti-slop, before memory
    "memory",
    "telemetry",
]
```

**Acceptance criteria:**
- [ ] Neuro-scorer middleware in chain after anti-slop
- [ ] Only runs on content-producing stages
- [ ] Scores available in pipeline result for downstream use

### Task 8: Tests
**Files:**
- `tests/titan/test_neuro_scorer.py` (new)
- `tests/titan/test_neuro_learning.py` (new)

**What:**
- Test ROI extraction maps all 4 dimensions from mock activation data
- Test normalization produces 0-1 scores
- Test cognitive load → cognitive ease inversion
- Test composite score computation with default and learned weights
- Test neural guidance prompt generation for weak dimensions
- Test daily reflection with mock outcome data
- Test neural rule creation at significance threshold
- Test segment profile computation
- Test feature flag on/off behavior
- Test lazy model load/unload (mock TRIBE v2)

**Acceptance criteria:**
- [ ] All tests pass with `PYTHONPATH=. pytest tests/titan/test_neuro_scorer.py tests/titan/test_neuro_learning.py -v`
- [ ] `ruff check titan/neuro/` clean

---

## New Files Summary

| File | Purpose |
|------|---------|
| `titan/neuro/__init__.py` | Neuro-scorer package |
| `titan/neuro/tribe_service.py` | TRIBE v2 model loading, lazy load/unload, text→activation |
| `titan/neuro/roi_extractor.py` | fsaverage5 atlas → ROI aggregation → 4 dimension raw scores |
| `titan/neuro/normalizer.py` | Running percentile normalization with warm-start |
| `titan/neuro/neuro_scorer.py` | Orchestrator: text → NeuroScores dataclass |
| `titan/neuro/learning_loop.py` | Daily reflection, rule extraction, weight updates, segment profiles |
| `scripts/migrations/024-neuro-scores.sql` | neuro_scores JSONB column on email_sequences |
| `scripts/tribe-validation-spike.py` | Throwaway MPS benchmark script |

---

## Success Criteria (from ROADMAP.md)

- [ ] MPS validation spike: TRIBE v2 inference < 8s per email on M4 32GB
- [ ] 4 cognitive dimensions scored for every outbound email
- [ ] Lazy model loading: memory freed when not scoring
- [ ] Neural gating: low-scoring emails re-drafted with dimension-specific guidance
- [ ] Closed-loop: correlations computed daily after 50+ scored emails
- [ ] Neural rules auto-extracted at p < 0.05, n >= 30
- [ ] Segment-specific profiles after 100+ emails per segment
- [ ] All content types scored (emails, sites, follow-ups)

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| TRIBE v2 too slow on M4 MPS | Medium | High | Spike validates first; batch scoring fallback; Haiku proxy as last resort |
| 32GB memory too tight | Low | High | Lazy load/unload; explicit gc.collect() + mps.empty_cache() |
| fsaverage5 atlas mismatch | Medium | Medium | Validate against TRIBE v2 output format in spike; adjust if needed |
| No correlation with conversions | Medium | High | First 100 emails are logging-only; feature flag kills cleanly; pivot to rubric-based scoring |
| CC-BY-NC challenged | Low | Medium | Internal tooling only; removable behind feature flag; no vendor lock-in |
| Cold-start garbage scores | Medium | Low | Warm-start normalization; 100-email logging-only phase before gating |

---

*Plan created: 2026-03-29 — TRIBE v2 Neuro-Scorer via mega-plan pipeline*
