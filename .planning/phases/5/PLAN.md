# Phase 5: RLM Recursive Context Retrieval

**Goal:** Email composition uses recursive context expansion to reference actual details from research (review quotes, pricing gaps, competitor names).
**Requirements:** RLM-01, RLM-02, RLM-03, RLM-04, RLM-05, RLM-06, RLM-07
**Depends on:** Phase 2 (anti-slop scorer as evaluator), Phase 4 (middleware for telemetry)
**Feature flag:** `RLM_ENABLED`

---

## Context

The core insight from the RLM paper (MIT, arXiv:2512.24601): treat context as an environment that the model queries recursively. Instead of single-pass email generation, RLM composes emails in a draft→evaluate→refine loop, pulling in specific research context at each iteration. This makes emails reference actual business details (Google review quotes, pricing gaps, competitor names) instead of generic AI slop.

Mem0 stores full research output (write path). Qdrant provides fast vector retrieval during composition (read path). The anti-slop scorer from Phase 2 serves as the evaluation function.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Email compose | `titan/pipeline/email_compose.py` | Single-pass LLM generation |
| Mem0 | Docker service | Running (docker-compose.yaml) |
| Qdrant | Docker service | Running (docker-compose.yaml) |
| Anti-slop scorer | `shared/anti_slop.py` | Built in Phase 2 |
| Budget guard | `tools/budget_guard.py` | Existing spend enforcement |
| rlm_composer.py | None | Does not exist |

---

## Tasks

### Task 1: Recursive compose loop (RLM-01)
**File:** `titan/pipeline/rlm_composer.py` (new)
**What:**

```python
class RLMComposer:
    """Recursive Language Model composer for emails.

    Implements draft → evaluate → refine loop with context expansion.
    Max 3 iterations. Returns highest-scoring version.
    """

    MAX_ITERATIONS = 3

    async def compose(
        self,
        lead: dict,
        research: dict,
        template: str = "",
    ) -> dict:
        """Compose email with recursive context retrieval."""
        versions: list[tuple[str, dict[str, float]]] = []

        # Retrieve relevant context from Qdrant
        context = await self._retrieve_context(lead, research)

        for iteration in range(self.MAX_ITERATIONS):
            # Draft: generate email with full context
            draft = await self._generate_draft(
                lead, research, context,
                previous_feedback=versions[-1][1] if versions else None,
                iteration=iteration,
            )

            # Evaluate: score with anti-slop (reuse Phase 2 scorer)
            scorer = AntiSlopScorer()
            scores = await scorer.score(draft, context="email")
            versions.append((draft, scores))

            # Check if good enough
            if _is_good_enough(scores, "email"):
                break

            # Refine context: pull additional details based on weak dimensions
            if scores.get("specificity", 0) < 0.6:
                context = await self._expand_context(lead, research, "specificity")

        # Return highest-scoring version
        best = max(versions, key=lambda v: _composite_score(v[1]))
        return {
            "email_body": best[0],
            "quality_scores": best[1],
            "iterations": len(versions),
            "context_sources": len(context),
        }

    async def _generate_draft(self, lead, research, context, previous_feedback, iteration):
        """Generate email draft with injected context."""
        # Build prompt with specific business details from context
        # Include previous feedback for refinement guidance
        ...

    async def _retrieve_context(self, lead, research) -> list[dict]:
        """Retrieve relevant context from Qdrant vector store."""
        ...

    async def _expand_context(self, lead, research, weak_dimension) -> list[dict]:
        """Pull additional context to strengthen weak scoring dimensions."""
        ...
```

**Acceptance criteria:**
- [ ] Recursive loop: draft → evaluate → refine, max 3 iterations
- [ ] Returns highest-scoring version (not latest)
- [ ] Context expansion targets weak dimensions

### Task 2: Mem0 write path (RLM-02)
**File:** `titan/pipeline/rlm_composer.py` (extend)
**What:**

Store full research output in Mem0 when leads are researched:

```python
async def store_research_context(lead_id: str, research: dict) -> str:
    """Store research output in Mem0 for later retrieval.

    Returns mem0_context_id for linking to leads table.
    """
    from mem0 import Memory

    m = Memory()
    # Store each research section as a separate memory
    for section, content in research.items():
        m.add(
            content,
            user_id=lead_id,
            metadata={"section": section, "lead_id": lead_id},
        )

    context_id = f"mem0_{lead_id}"
    # Update leads table with context_id
    await execute(
        "UPDATE leads SET mem0_context_id = %s WHERE id = %s",
        (context_id, lead_id),
    )
    return context_id
```

Add `mem0_context_id` column to leads table via migration.

**Acceptance criteria:**
- [ ] Research output stored in Mem0 per lead
- [ ] `mem0_context_id` linked on leads table
- [ ] Stored context includes: business details, reviews, competitors, pricing

### Task 3: Qdrant read path (RLM-03)
**File:** `titan/pipeline/rlm_composer.py` (extend)
**What:**

Fast vector retrieval during email composition:

```python
async def _retrieve_context(self, lead: dict, research: dict) -> list[dict]:
    """Retrieve relevant context from Qdrant."""
    from qdrant_client import QdrantClient

    client = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))

    # Build query from lead info
    query = f"{lead.get('company_name', '')} {lead.get('industry', '')} {lead.get('pain_points', '')}"

    results = client.search(
        collection_name="research_contexts",
        query_text=query,
        limit=10,
    )

    return [{"content": r.payload.get("content", ""), "score": r.score} for r in results]
```

**Acceptance criteria:**
- [ ] Qdrant retrieval returns relevant context in < 200ms
- [ ] Context includes specific business details (not generic)
- [ ] Collection created and indexed properly

### Task 4: Per-email + monthly spend caps (RLM-04)
**File:** `titan/pipeline/rlm_composer.py` (extend)
**What:**

```python
RLM_PER_EMAIL_CAP = Decimal("0.08")   # $0.08 per email max
RLM_MONTHLY_CAP = Decimal("100.00")   # $100/month global RLM spend

async def _check_budget(self) -> bool:
    """Check if RLM is within budget."""
    # Per-email: track cost of current composition
    if self._current_cost > RLM_PER_EMAIL_CAP:
        logger.warning("RLM per-email cap exceeded, returning best draft so far")
        return False

    # Monthly: query llm_metrics for RLM spend this month
    monthly_spend = await get_monthly_rlm_spend()
    if monthly_spend > RLM_MONTHLY_CAP:
        logger.warning("RLM monthly cap exceeded, falling back to single-pass")
        return False

    return True
```

Integrate with `tools/budget_guard.py` for consistent enforcement.

**Acceptance criteria:**
- [ ] Per-email spend capped at $0.08
- [ ] Monthly RLM spend capped at $100
- [ ] Budget exceeded → fall back to single-pass compose
- [ ] Cost tracked in llm_metrics (Phase 0b)

### Task 5: Haiku evaluation reusing anti-slop (RLM-05)
**File:** `titan/pipeline/rlm_composer.py` (wiring)
**What:**

The evaluation step reuses `AntiSlopScorer` from Phase 2:
- Same 5-dimension scoring (clarity, specificity, authenticity, value_density, slop_score)
- Same Haiku model for cost efficiency
- Same thresholds and good-enough logic

No new code needed — just wire `AntiSlopScorer.score()` into the evaluate step of the RLM loop.

**Acceptance criteria:**
- [ ] Evaluation uses Phase 2 AntiSlopScorer (no duplication)
- [ ] Haiku used for evaluation (cost efficient)

### Task 6: Shadow mode A/B testing (RLM-06)
**File:** `titan/pipeline/email_compose.py` (modify)
**What:**

```python
async def compose_email(lead, research, ...):
    if feature_flag("RLM_ENABLED"):
        if _is_shadow_mode():
            # Run BOTH: original + RLM, compare results
            original = await _original_compose(lead, research)
            rlm = await RLMComposer().compose(lead, research)

            # Log comparison data
            await _log_ab_comparison(lead["id"], original, rlm)

            # Return original (shadow = safe)
            return original
        else:
            # Full RLM mode
            return await RLMComposer().compose(lead, research)
    else:
        return await _original_compose(lead, research)

def _is_shadow_mode() -> bool:
    """Shadow mode: first 7 days after RLM_ENABLED."""
    return os.getenv("RLM_SHADOW_MODE", "false").lower() == "true"
```

Shadow mode captures comparison data for 1 week before cutover.

**Acceptance criteria:**
- [ ] Shadow mode runs both compose paths
- [ ] Comparison data logged (original scores vs RLM scores)
- [ ] Shadow mode returns original email (safe)
- [ ] Shadow period configurable (default 7 days)

### Task 7: Feature flag with instant rollback (RLM-07)
**File:** `titan/pipeline/email_compose.py` (verify)
**What:**
- `RLM_ENABLED=false` → single-pass compose (existing behavior)
- `RLM_ENABLED=true` + `RLM_SHADOW_MODE=true` → shadow A/B testing
- `RLM_ENABLED=true` + `RLM_SHADOW_MODE=false` → full RLM compose
- Instant rollback: set `RLM_ENABLED=false` in system_config, takes effect next email

**Acceptance criteria:**
- [ ] Feature flag toggles cleanly between RLM and original compose
- [ ] No code changes needed to rollback
- [ ] State stored in system_config (not env var) for runtime toggle

### Task 8: Migration + Tests
**Files:**
- `scripts/migrations/021-rlm-context.sql` (new) — Add `mem0_context_id` to leads
- `tests/titan/test_rlm_composer.py` (new)

**What:**
```sql
ALTER TABLE leads ADD COLUMN IF NOT EXISTS mem0_context_id TEXT;
```

Tests:
- Test recursive compose produces higher-scoring emails than single-pass
- Test budget cap stops iteration
- Test shadow mode logs comparison data
- Test Qdrant retrieval returns relevant context
- Test feature flag on/off behavior

**Acceptance criteria:**
- [ ] All tests pass
- [ ] RLM emails score +15% higher specificity than single-pass (on test corpus)

---

## Success Criteria (from ROADMAP.md)

- [ ] RLM emails score +15% higher specificity than single-pass (A/B test)
- [ ] No email exceeds $0.08 budget cap
- [ ] Monthly RLM spend stays under $100 cap
- [ ] Shadow mode captures comparison data for 1 week
- [ ] Feature flag toggles cleanly between RLM and original compose

---

*Plan created: 2026-03-29*
